import * as k8s from "@kubernetes/client-node";
import type { KubernetesProviderConfig } from "./config.js";
import { resolveConfig } from "./config.js";
import {
  buildPodManifest,
  buildNamespaceManifest,
  buildNetworkPolicyManifest,
  buildResourceQuotaManifest,
} from "./manifests.js";
import { KubernetesSandbox } from "./sandbox.js";
import {
  generatePodName,
  generateNamespaceName,
  parseSandboxId,
  buildSandboxId,
  MANAGED_SELECTOR,
  LABEL_MANAGED_BY,
  MANAGED_BY_VALUE,
  networkPolicyName,
} from "./utils.js";
import { pollUntil } from "./utils.js";
import {
  SandboxNotFoundError,
  SandboxStartupTimeoutError,
  NamespaceConflictError,
} from "./errors.js";

/** A single entry in a sandbox list response. */
export interface SandboxInfo {
  id: string;
  podName: string;
  namespace: string;
  phase: string;
  createdAt?: Date;
}

/** Paginated list response. */
export interface SandboxListResponse {
  sandboxes: SandboxInfo[];
  /** Next cursor for pagination (Pod name to continue from). */
  nextCursor?: string;
}

/**
 * Manages the lifecycle of Kubernetes sandbox Pods.
 *
 * ```typescript
 * const provider = new KubernetesProvider({ image: "python:3.12-slim" });
 * const sandbox  = await provider.getOrCreate();
 * const result   = await sandbox.execute("python3 -c 'print(42)'");
 * await provider.delete(sandbox.id);
 * ```
 */
export class KubernetesProvider {
  private readonly config: KubernetesProviderConfig;
  private readonly kubeConfig: k8s.KubeConfig;
  private readonly coreApi: k8s.CoreV1Api;
  private readonly networkingApi: k8s.NetworkingV1Api;

  constructor(config?: Partial<KubernetesProviderConfig>) {
    this.config = resolveConfig(config);

    // Build KubeConfig from path/context or defaults.
    this.kubeConfig = new k8s.KubeConfig();
    if (this.config.kubeconfigPath) {
      this.kubeConfig.loadFromFile(this.config.kubeconfigPath);
    } else {
      this.kubeConfig.loadFromDefault();
    }
    if (this.config.context) {
      this.kubeConfig.setCurrentContext(this.config.context);
    }

    this.coreApi = this.kubeConfig.makeApiClient(k8s.CoreV1Api);
    this.networkingApi = this.kubeConfig.makeApiClient(k8s.NetworkingV1Api);
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  /**
   * Get an existing sandbox by ID or create a new one.
   *
   * - If `sandboxId` is provided, reconnects to the existing Pod.
   * - If `sandboxId` is omitted, creates a brand-new Pod (and optionally its
   *   namespace and NetworkPolicy).
   *
   * @param sandboxId - Optional existing sandbox ID to reconnect to.
   * @param options   - Reserved for future provider-specific options.
   */
  async getOrCreate(
    sandboxId?: string,
    _options?: Record<string, unknown>
  ): Promise<KubernetesSandbox> {
    if (sandboxId) {
      return this.reconnect(sandboxId);
    }
    return this.create();
  }

  /**
   * List active sandbox Pods, ordered by creation timestamp.
   *
   * @param cursor - Continuation token for pagination.
   */
  async list(_cursor?: string): Promise<SandboxListResponse> {
    const namespace = this.config.namespacePerSandbox ? undefined : this.config.namespace;

    let pods: k8s.V1Pod[];
    if (namespace) {
      const res = await this.coreApi.listNamespacedPod({
        namespace,
        labelSelector: MANAGED_SELECTOR,
      });
      pods = res.items;
    } else {
      // namespacePerSandbox=true: list across all namespaces
      const res = await this.coreApi.listPodForAllNamespaces({
        labelSelector: MANAGED_SELECTOR,
      });
      pods = res.items;
    }

    const sandboxes: SandboxInfo[] = pods
      .filter((pod) => pod.metadata?.name && pod.metadata?.namespace)
      .map((pod) => {
        const podName = pod.metadata!.name!;
        const ns = pod.metadata!.namespace!;
        const id = buildSandboxId(ns, podName, this.config.namespacePerSandbox);
        return {
          id,
          podName,
          namespace: ns,
          phase: pod.status?.phase ?? "Unknown",
          createdAt: pod.metadata?.creationTimestamp,
        };
      });

    return { sandboxes };
  }

  /**
   * Delete a sandbox Pod (and its namespace if `namespacePerSandbox=true`).
   * This is idempotent: deleting a non-existent sandbox is a no-op.
   *
   * @param sandboxId - The sandbox ID to delete.
   */
  async delete(sandboxId: string): Promise<void> {
    const { namespace, podName } = parseSandboxId(sandboxId, this.config.namespace);

    if (this.config.namespacePerSandbox) {
      // Deleting the namespace cascades to the Pod and NetworkPolicy.
      await this.deleteNamespaceSafe(namespace);
    } else {
      // Delete Pod and NetworkPolicy individually.
      await Promise.all([
        this.deletePodSafe(namespace, podName),
        this.deleteNetworkPolicySafe(namespace, sandboxId),
      ]);
    }
  }

  // ── Private helpers ────────────────────────────────────────────────────────

  /** Create a fresh sandbox Pod (and namespace/NetworkPolicy if configured). */
  private async create(): Promise<KubernetesSandbox> {
    const podName = generatePodName();
    let namespace = this.config.namespace;

    if (this.config.namespacePerSandbox) {
      namespace = generateNamespaceName();
      await this.ensureNamespace(namespace, buildSandboxId(namespace, podName, true));
    } else {
      await this.ensureNamespace(namespace, "shared");
    }

    const sandboxId = buildSandboxId(namespace, podName, this.config.namespacePerSandbox);
    const podManifest = buildPodManifest(podName, namespace, sandboxId, this.config);

    try {
      await this.coreApi.createNamespacedPod({ namespace, body: podManifest });
    } catch (err) {
      // Attempt cleanup before rethrowing
      await this.deletePodSafe(namespace, podName).catch(() => undefined);
      throw err;
    }

    if (this.config.blockNetwork) {
      const netPol = buildNetworkPolicyManifest(namespace, sandboxId);
      try {
        await this.networkingApi.createNamespacedNetworkPolicy({ namespace, body: netPol });
      } catch (err) {
        // Non-fatal: log and continue (pod is already running)
        console.warn(
          `[langchain-kubernetes] Failed to create NetworkPolicy for ${sandboxId}: ${String(err)}`
        );
      }
    }

    // Wait for the Pod to reach Running phase
    await this.waitForRunning(namespace, podName, sandboxId);

    return new KubernetesSandbox({
      podName,
      namespace,
      namespacePerSandbox: this.config.namespacePerSandbox,
      kubeConfig: this.kubeConfig,
      execConfig: this.config.execConfig,
    });
  }

  /** Reconnect to an existing sandbox by ID. */
  private async reconnect(sandboxId: string): Promise<KubernetesSandbox> {
    const { namespace, podName } = parseSandboxId(sandboxId, this.config.namespace);

    let pod: k8s.V1Pod;
    try {
      pod = await this.coreApi.readNamespacedPod({ name: podName, namespace });
    } catch (err: unknown) {
      if (isNotFoundError(err)) {
        throw new SandboxNotFoundError(sandboxId);
      }
      throw err;
    }

    const phase = pod.status?.phase;
    if (phase !== "Running") {
      throw new SandboxNotFoundError(
        `${sandboxId} (phase: ${phase ?? "Unknown"})`
      );
    }

    return new KubernetesSandbox({
      podName,
      namespace,
      namespacePerSandbox: this.config.namespacePerSandbox,
      kubeConfig: this.kubeConfig,
      execConfig: this.config.execConfig,
    });
  }

  /**
   * Ensure a namespace exists. For the shared namespace strategy, creates it
   * if missing; for the per-sandbox strategy, always creates fresh.
   * Throws NamespaceConflictError if the namespace exists but isn't ours.
   */
  private async ensureNamespace(
    namespaceName: string,
    sandboxId: string
  ): Promise<void> {
    try {
      const ns = await this.coreApi.readNamespace({ name: namespaceName });
      const existingLabels = ns.metadata?.labels ?? {};

      // Namespace exists — verify it's managed by us
      if (existingLabels[LABEL_MANAGED_BY] !== MANAGED_BY_VALUE) {
        throw new NamespaceConflictError(namespaceName);
      }
      // Already ours — nothing to do.
    } catch (err: unknown) {
      if (err instanceof NamespaceConflictError) throw err;
      if (isNotFoundError(err)) {
        // Create it
        const nsManifest = buildNamespaceManifest(namespaceName, sandboxId, this.config);
        await this.coreApi.createNamespace({ body: nsManifest });

        if (this.config.namespacePerSandbox) {
          // Also create a ResourceQuota
          const quota = buildResourceQuotaManifest(namespaceName, sandboxId, this.config);
          await this.coreApi.createNamespacedResourceQuota({ namespace: namespaceName, body: quota });
        }
      } else {
        throw err;
      }
    }
  }

  /**
   * Poll until the Pod is in Running phase, or throw SandboxStartupTimeoutError.
   * Cleans up the Pod on timeout.
   */
  private async waitForRunning(
    namespace: string,
    podName: string,
    sandboxId: string
  ): Promise<void> {
    const timeoutMs = this.config.startupTimeoutSeconds * 1000;

    try {
      await pollUntil(
        async () => {
          const pod = await this.coreApi.readNamespacedPod({ name: podName, namespace });
          const phase = pod.status?.phase;
          return phase === "Running" ? true : false;
        },
        2000,
        timeoutMs,
        `timeout`
      );
    } catch {
      // Clean up and re-throw as SandboxStartupTimeoutError
      await this.deletePodSafe(namespace, podName).catch(() => undefined);
      throw new SandboxStartupTimeoutError(sandboxId, this.config.startupTimeoutSeconds);
    }
  }

  /** Delete a Pod, ignoring 404. */
  private async deletePodSafe(namespace: string, podName: string): Promise<void> {
    try {
      await this.coreApi.deleteNamespacedPod({ name: podName, namespace });
    } catch (err: unknown) {
      if (!isNotFoundError(err)) throw err;
    }
  }

  /** Delete a NetworkPolicy, ignoring 404. */
  private async deleteNetworkPolicySafe(
    namespace: string,
    sandboxId: string
  ): Promise<void> {
    const name = networkPolicyName(sandboxId);
    try {
      await this.networkingApi.deleteNamespacedNetworkPolicy({ name, namespace });
    } catch (err: unknown) {
      if (!isNotFoundError(err)) throw err;
    }
  }

  /** Delete a Namespace, ignoring 404. */
  private async deleteNamespaceSafe(namespaceName: string): Promise<void> {
    try {
      await this.coreApi.deleteNamespace({ name: namespaceName });
    } catch (err: unknown) {
      if (!isNotFoundError(err)) throw err;
    }
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Check whether a Kubernetes API error is a 404 Not Found. */
function isNotFoundError(err: unknown): boolean {
  if (err && typeof err === "object") {
    // @kubernetes/client-node v1.x throws with statusCode or response.statusCode
    const e = err as { statusCode?: number; code?: number };
    return e.statusCode === 404 || e.code === 404;
  }
  return false;
}
