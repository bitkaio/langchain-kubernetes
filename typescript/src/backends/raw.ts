import type {
  ExecuteResponse,
  FileUploadResponse,
  FileDownloadResponse,
} from "deepagents";
import type { KubernetesProviderConfig } from "../config.js";
import { resolveExecuteConfig } from "../config.js";
import {
  MissingDependencyError,
  SandboxNotFoundError,
  SandboxStartupTimeoutError,
  NamespaceConflictError,
} from "../errors.js";
import {
  generatePodName,
  generateNamespaceName,
  buildSandboxId,
  parseSandboxId,
  LABEL_MANAGED_BY,
  MANAGED_BY_VALUE,
  MANAGED_SELECTOR,
  networkPolicyName,
  pollUntil,
} from "../utils.js";
import type { KubernetesBackend } from "./types.js";

/** Info about a raw-mode sandbox Pod. */
export interface RawSandboxInfo {
  id: string;
  podName: string;
  namespace: string;
  phase: string;
  createdAt?: Date;
}

/**
 * Backend that manages sandbox Pods directly via `@kubernetes/client-node`.
 *
 * This is the fallback mode — it works on any Kubernetes cluster without
 * requiring the agent-sandbox controller or CRDs.
 */
export class RawK8sBackend implements KubernetesBackend {
  readonly id: string;

  private readonly podName: string;
  private readonly namespace: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private readonly transport: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private readonly kubeConfig: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private readonly coreApi: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private readonly networkingApi: any;
  private readonly config: KubernetesProviderConfig;

  private constructor(
    podName: string,
    namespace: string,
    config: KubernetesProviderConfig,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    kubeConfig: any,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    coreApi: any,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    networkingApi: any,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    transport: any
  ) {
    this.podName = podName;
    this.namespace = namespace;
    this.config = config;
    this.kubeConfig = kubeConfig;
    this.coreApi = coreApi;
    this.networkingApi = networkingApi;
    this.transport = transport;
    this.id = buildSandboxId(
      namespace,
      podName,
      config.namespacePerSandbox
    );
  }

  /**
   * Create a new sandbox Pod and wait for it to become Running.
   *
   * @param config - Full resolved provider config.
   * @throws {MissingDependencyError} When `@kubernetes/client-node` is not installed.
   */
  static async create(config: KubernetesProviderConfig): Promise<RawK8sBackend> {
    const { k8s, tarStream } = await loadRawDependencies();

    const kubeConfig = buildKubeConfig(k8s, config);
    const coreApi = kubeConfig.makeApiClient(k8s.CoreV1Api);
    const networkingApi = kubeConfig.makeApiClient(k8s.NetworkingV1Api);

    const {
      buildPodManifest,
      buildNamespaceManifest,
      buildNetworkPolicyManifest,
      buildResourceQuotaManifest,
    } = await import("./raw-manifests.js");
    const { ExecTransport } = await import("./raw-transport.js");
    void tarStream; // imported for side-effect check

    const podName = generatePodName();
    let namespace = config.namespace;

    if (config.namespacePerSandbox) {
      namespace = generateNamespaceName();
      const sandboxId = buildSandboxId(namespace, podName, true);
      await ensureNamespace(coreApi, namespace, sandboxId, config, buildNamespaceManifest, buildResourceQuotaManifest);
    } else {
      const sandboxId = buildSandboxId(namespace, podName, false);
      await ensureNamespace(coreApi, namespace, sandboxId, config, buildNamespaceManifest, buildResourceQuotaManifest);
    }

    const sandboxId = buildSandboxId(namespace, podName, config.namespacePerSandbox);
    const podManifest = buildPodManifest(podName, namespace, sandboxId, config);

    try {
      await coreApi.createNamespacedPod({ namespace, body: podManifest });
    } catch (err) {
      await deletePodSafe(coreApi, namespace, podName).catch(() => undefined);
      throw err;
    }

    if (config.blockNetwork) {
      const netPol = buildNetworkPolicyManifest(namespace, sandboxId);
      try {
        await networkingApi.createNamespacedNetworkPolicy({ namespace, body: netPol });
      } catch (err) {
        console.warn(
          `[langchain-kubernetes] Failed to create NetworkPolicy for ${sandboxId}: ${String(err)}`
        );
      }
    }

    await waitForRunning(coreApi, namespace, podName, sandboxId, config);

    const execConfig = resolveExecuteConfig(config.execConfig);
    const transport = new ExecTransport(kubeConfig, execConfig);

    return new RawK8sBackend(podName, namespace, config, kubeConfig, coreApi, networkingApi, transport);
  }

  /**
   * Reconnect to an existing Running sandbox Pod.
   *
   * @param sandboxId - The sandbox ID to reconnect to.
   * @param config    - Full resolved provider config.
   * @throws {MissingDependencyError} When `@kubernetes/client-node` is not installed.
   * @throws {SandboxNotFoundError} When the Pod doesn't exist or isn't Running.
   */
  static async reconnect(
    sandboxId: string,
    config: KubernetesProviderConfig
  ): Promise<RawK8sBackend> {
    const { k8s } = await loadRawDependencies();

    const kubeConfig = buildKubeConfig(k8s, config);
    const coreApi = kubeConfig.makeApiClient(k8s.CoreV1Api);
    const networkingApi = kubeConfig.makeApiClient(k8s.NetworkingV1Api);

    const { namespace, podName } = parseSandboxId(sandboxId, config.namespace);

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let pod: any;
    try {
      pod = await coreApi.readNamespacedPod({ name: podName, namespace });
    } catch (err: unknown) {
      if (isNotFoundError(err)) {
        throw new SandboxNotFoundError(sandboxId);
      }
      throw err;
    }

    const phase = pod.status?.phase;
    if (phase !== "Running") {
      throw new SandboxNotFoundError(`${sandboxId} (phase: ${phase ?? "Unknown"})`);
    }

    const { ExecTransport } = await import("./raw-transport.js");
    const execConfig = resolveExecuteConfig(config.execConfig);
    const transport = new ExecTransport(kubeConfig, execConfig);

    return new RawK8sBackend(podName, namespace, config, kubeConfig, coreApi, networkingApi, transport);
  }

  /**
   * List all active sandbox Pods.
   *
   * @param config - Full resolved provider config.
   */
  static async list(config: KubernetesProviderConfig): Promise<RawSandboxInfo[]> {
    const { k8s } = await loadRawDependencies();
    const kubeConfig = buildKubeConfig(k8s, config);
    const coreApi = kubeConfig.makeApiClient(k8s.CoreV1Api);

    const namespace = config.namespacePerSandbox ? undefined : config.namespace;

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let pods: any[];
    if (namespace) {
      const res = await coreApi.listNamespacedPod({
        namespace,
        labelSelector: MANAGED_SELECTOR,
      });
      pods = res.items;
    } else {
      const res = await coreApi.listPodForAllNamespaces({
        labelSelector: MANAGED_SELECTOR,
      });
      pods = res.items;
    }

    return pods
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      .filter((pod: any) => pod.metadata?.name && pod.metadata?.namespace)
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      .map((pod: any) => {
        const pName = pod.metadata!.name!;
        const ns = pod.metadata!.namespace!;
        return {
          id: buildSandboxId(ns, pName, config.namespacePerSandbox),
          podName: pName,
          namespace: ns,
          phase: pod.status?.phase ?? "Unknown",
          createdAt: pod.metadata?.creationTimestamp,
        };
      });
  }

  /**
   * Delete a sandbox by ID.
   *
   * @param sandboxId - The sandbox ID to delete.
   * @param config    - Full resolved provider config.
   */
  static async deleteSandbox(
    sandboxId: string,
    config: KubernetesProviderConfig
  ): Promise<void> {
    const { k8s } = await loadRawDependencies();
    const kubeConfig = buildKubeConfig(k8s, config);
    const coreApi = kubeConfig.makeApiClient(k8s.CoreV1Api);
    const networkingApi = kubeConfig.makeApiClient(k8s.NetworkingV1Api);

    const { namespace, podName } = parseSandboxId(sandboxId, config.namespace);

    if (config.namespacePerSandbox) {
      await deleteNamespaceSafe(coreApi, namespace);
    } else {
      await Promise.all([
        deletePodSafe(coreApi, namespace, podName),
        deleteNetworkPolicySafe(networkingApi, namespace, sandboxId),
      ]);
    }
  }

  // ── KubernetesBackend implementation ────────────────────────────────────────

  /** Execute a shell command inside the sandbox Pod. */
  async execute(
    command: string,
    options?: { timeout?: number }
  ): Promise<ExecuteResponse> {
    const result = await this.transport.runCommand(
      this.namespace,
      this.podName,
      command,
      options?.timeout
    );
    return {
      output: result.output,
      exitCode: result.exitCode,
      truncated: result.truncated,
    };
  }

  /** Upload files using tar-over-exec. */
  async uploadFiles(
    files: Array<[string, Uint8Array]>
  ): Promise<FileUploadResponse[]> {
    try {
      await this.transport.uploadFiles(this.namespace, this.podName, files);
      return files.map(([path]) => ({ path, error: null }));
    } catch {
      return files.map(([path]) => ({
        path,
        error: "file_not_found" as const,
      }));
    }
  }

  /** Download files using tar-over-exec. */
  async downloadFiles(paths: string[]): Promise<FileDownloadResponse[]> {
    try {
      const downloaded = await this.transport.downloadFiles(
        this.namespace,
        this.podName,
        paths
      );
      const downloadedMap = new Map(
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        downloaded.map((f: any) => [f.path, f.content])
      );

      return paths.map((path) => {
        const content = downloadedMap.get(path) as Uint8Array | undefined;
        if (content !== undefined) {
          return { path, content, error: null };
        }
        return { path, content: null, error: "file_not_found" as const };
      });
    } catch {
      return paths.map((path) => ({
        path,
        content: null,
        error: "file_not_found" as const,
      }));
    }
  }

  /** Delete the Pod and associated resources. */
  async cleanup(): Promise<void> {
    await RawK8sBackend.deleteSandbox(this.id, this.config);
  }
}

// ── Private helpers ────────────────────────────────────────────────────────────

/** Dynamically import @kubernetes/client-node and tar-stream. */
async function loadRawDependencies(): Promise<{
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  k8s: any;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  tarStream: any;
}> {
  try {
    const [k8s, tarStream] = await Promise.all([
      import("@kubernetes/client-node"),
      import("tar-stream"),
    ]);
    return { k8s, tarStream };
  } catch {
    throw new MissingDependencyError(
      "raw",
      "npm install @kubernetes/client-node tar-stream"
    );
  }
}

/** Build a KubeConfig from provider config. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function buildKubeConfig(k8s: any, config: KubernetesProviderConfig): any {
  const kubeConfig = new k8s.KubeConfig();
  if (config.kubeconfigPath) {
    kubeConfig.loadFromFile(config.kubeconfigPath);
  } else {
    kubeConfig.loadFromDefault();
  }
  if (config.context) {
    kubeConfig.setCurrentContext(config.context);
  }
  return kubeConfig;
}

/** Ensure a namespace exists and is owned by deepagents. */
async function ensureNamespace(
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  coreApi: any,
  namespaceName: string,
  sandboxId: string,
  config: KubernetesProviderConfig,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  buildNamespaceManifest: any,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  buildResourceQuotaManifest: any
): Promise<void> {
  try {
    const ns = await coreApi.readNamespace({ name: namespaceName });
    const existingLabels = ns.metadata?.labels ?? {};
    if (existingLabels[LABEL_MANAGED_BY] !== MANAGED_BY_VALUE) {
      throw new NamespaceConflictError(namespaceName);
    }
  } catch (err: unknown) {
    if (err instanceof NamespaceConflictError) throw err;
    if (isNotFoundError(err)) {
      const nsManifest = buildNamespaceManifest(namespaceName, sandboxId, config);
      await coreApi.createNamespace({ body: nsManifest });

      if (config.namespacePerSandbox) {
        const quota = buildResourceQuotaManifest(namespaceName, sandboxId, config);
        await coreApi.createNamespacedResourceQuota({ namespace: namespaceName, body: quota });
      }
    } else {
      throw err;
    }
  }
}

/** Poll until Pod is Running, or throw SandboxStartupTimeoutError. */
async function waitForRunning(
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  coreApi: any,
  namespace: string,
  podName: string,
  sandboxId: string,
  config: KubernetesProviderConfig
): Promise<void> {
  const timeoutMs = config.startupTimeoutSeconds * 1000;
  try {
    await pollUntil(
      async () => {
        const pod = await coreApi.readNamespacedPod({ name: podName, namespace });
        return pod.status?.phase === "Running" ? true : false;
      },
      2000,
      timeoutMs,
      "timeout"
    );
  } catch {
    await deletePodSafe(coreApi, namespace, podName).catch(() => undefined);
    throw new SandboxStartupTimeoutError(sandboxId, config.startupTimeoutSeconds);
  }
}

/** Delete a Pod, ignoring 404. */
async function deletePodSafe(
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  coreApi: any,
  namespace: string,
  podName: string
): Promise<void> {
  try {
    await coreApi.deleteNamespacedPod({ name: podName, namespace });
  } catch (err: unknown) {
    if (!isNotFoundError(err)) throw err;
  }
}

/** Delete a NetworkPolicy, ignoring 404. */
async function deleteNetworkPolicySafe(
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  networkingApi: any,
  namespace: string,
  sandboxId: string
): Promise<void> {
  const name = networkPolicyName(sandboxId);
  try {
    await networkingApi.deleteNamespacedNetworkPolicy({ name, namespace });
  } catch (err: unknown) {
    if (!isNotFoundError(err)) throw err;
  }
}

/** Delete a Namespace, ignoring 404. */
async function deleteNamespaceSafe(
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  coreApi: any,
  namespaceName: string
): Promise<void> {
  try {
    await coreApi.deleteNamespace({ name: namespaceName });
  } catch (err: unknown) {
    if (!isNotFoundError(err)) throw err;
  }
}

/** Check whether a Kubernetes API error is a 404 Not Found. */
function isNotFoundError(err: unknown): boolean {
  if (err && typeof err === "object") {
    const e = err as { statusCode?: number; code?: number };
    return e.statusCode === 404 || e.code === 404;
  }
  return false;
}
