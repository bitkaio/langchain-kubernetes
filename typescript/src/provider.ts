import type { KubernetesProviderConfig } from "./config.js";
import { resolveConfig, validateConfig } from "./config.js";
import { KubernetesSandbox } from "./sandbox.js";
import { AgentSandboxBackend } from "./backends/agent-sandbox.js";
import { RawK8sBackend } from "./backends/raw.js";
import type { SandboxInfo as AgentSandboxInfo } from "./router-client.js";
import type { RawSandboxInfo } from "./backends/raw.js";
import { SandboxNotFoundError } from "./errors.js";

/** A single entry in a sandbox list response. */
export interface SandboxInfo {
  id: string;
  namespace: string;
  phase?: string;
  createdAt?: Date;
}

/** Paginated list response. */
export interface SandboxListResponse {
  sandboxes: SandboxInfo[];
}

/**
 * Manages the lifecycle of Kubernetes sandbox environments.
 *
 * Supports two backend modes, selected via `config.mode`:
 *
 * **`agent-sandbox` mode** (default, recommended):
 * - Requires: `routerUrl` and `templateName` in config.
 * - Requires: `kubernetes-sigs/agent-sandbox` controller + CRDs installed in cluster.
 * - Benefits: warm pools, gVisor/Kata isolation, sub-second startup.
 * - No extra npm dependencies — uses built-in `fetch()`.
 *
 * **`raw` mode** (fallback):
 * - Requires: `@kubernetes/client-node` and `tar-stream` installed.
 * - Works on any cluster — no CRDs needed.
 * - Full control over Pod spec via config.
 *
 * ```typescript
 * // agent-sandbox mode
 * const provider = new KubernetesProvider({
 *   mode: "agent-sandbox",
 *   routerUrl: "http://sandbox-router-svc.default.svc.cluster.local:8080",
 *   templateName: "python-sandbox-template",
 * });
 *
 * // raw mode
 * const provider = new KubernetesProvider({
 *   mode: "raw",
 *   image: "python:3.12-slim",
 * });
 *
 * const sandbox = await provider.getOrCreate();
 * const result  = await sandbox.execute("python3 -c 'print(42)'");
 * await provider.delete(sandbox.id);
 * ```
 */
export class KubernetesProvider {
  private readonly config: KubernetesProviderConfig;

  constructor(config?: Partial<KubernetesProviderConfig>) {
    this.config = resolveConfig(config);
    validateConfig(this.config);
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  /**
   * Get an existing sandbox by ID or create a new one.
   *
   * - If `sandboxId` is provided, reconnects to the existing sandbox.
   * - If `sandboxId` is omitted, creates a brand-new sandbox.
   *
   * @param sandboxId - Optional existing sandbox ID to reconnect to.
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
   * List active sandboxes.
   */
  async list(): Promise<SandboxListResponse> {
    const mode = this.config.mode ?? "agent-sandbox";

    if (mode === "agent-sandbox") {
      const infos: AgentSandboxInfo[] = await AgentSandboxBackend.list(this.config);
      const sandboxes: SandboxInfo[] = infos.map((info) => ({
        id: info.name,
        namespace: info.namespace,
        phase: info.ready ? "Ready" : "Pending",
      }));
      return { sandboxes };
    } else {
      const infos: RawSandboxInfo[] = await RawK8sBackend.list(this.config);
      const sandboxes: SandboxInfo[] = infos.map((info) => ({
        id: info.id,
        namespace: info.namespace,
        phase: info.phase,
        createdAt: info.createdAt,
      }));
      return { sandboxes };
    }
  }

  /**
   * Delete a sandbox. Idempotent — deleting a non-existent sandbox is a no-op.
   *
   * @param sandboxId - The sandbox ID to delete.
   */
  async delete(sandboxId: string): Promise<void> {
    const mode = this.config.mode ?? "agent-sandbox";

    if (mode === "agent-sandbox") {
      await AgentSandboxBackend.deleteSandbox(sandboxId, this.config);
    } else {
      await RawK8sBackend.deleteSandbox(sandboxId, this.config);
    }
  }

  // ── Private helpers ────────────────────────────────────────────────────────

  /** Create a fresh sandbox. */
  private async create(): Promise<KubernetesSandbox> {
    const mode = this.config.mode ?? "agent-sandbox";

    if (mode === "agent-sandbox") {
      const backend = await AgentSandboxBackend.create(this.config);
      return new KubernetesSandbox(backend);
    } else {
      const backend = await RawK8sBackend.create(this.config);
      return new KubernetesSandbox(backend);
    }
  }

  /** Reconnect to an existing sandbox by ID. */
  private async reconnect(sandboxId: string): Promise<KubernetesSandbox> {
    const mode = this.config.mode ?? "agent-sandbox";

    if (mode === "agent-sandbox") {
      const backend = await AgentSandboxBackend.reconnect(sandboxId, this.config);
      return new KubernetesSandbox(backend);
    } else {
      try {
        const backend = await RawK8sBackend.reconnect(sandboxId, this.config);
        return new KubernetesSandbox(backend);
      } catch (err: unknown) {
        if (err instanceof SandboxNotFoundError) throw err;
        throw err;
      }
    }
  }
}
