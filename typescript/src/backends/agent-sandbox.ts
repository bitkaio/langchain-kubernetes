import type {
  ExecuteResponse,
  FileUploadResponse,
  FileDownloadResponse,
} from "deepagents";
import type { KubernetesProviderConfig } from "../config.js";
import {
  SandboxRouterError,
} from "../errors.js";
import {
  SandboxRouterClient,
  type SandboxInfo,
} from "../router-client.js";
import type { KubernetesBackend } from "./types.js";

/**
 * Backend that communicates with a sandbox through the `kubernetes-sigs/agent-sandbox`
 * sandbox-router HTTP gateway.
 *
 * Uses only the built-in `fetch()` — no `@kubernetes/client-node` required.
 *
 * The backend:
 * 1. Creates a `SandboxClaim` CRD in the cluster (via the Kubernetes REST API).
 * 2. Waits for the corresponding `Sandbox` to become Ready.
 * 3. Routes all runtime operations (execute, file I/O) through the sandbox-router
 *    using the claim name as `X-Sandbox-ID`.
 * 4. Deletes the `SandboxClaim` on cleanup, which triggers sandbox teardown.
 */
export class AgentSandboxBackend implements KubernetesBackend {
  readonly id: string;

  private readonly client: SandboxRouterClient;
  private readonly sandboxName: string;
  private readonly config: KubernetesProviderConfig;

  private constructor(
    sandboxName: string,
    client: SandboxRouterClient,
    config: KubernetesProviderConfig
  ) {
    this.sandboxName = sandboxName;
    this.client = client;
    this.config = config;
    this.id = sandboxName;
  }

  /**
   * Create a new sandbox by provisioning a SandboxClaim and waiting for it
   * to be ready.
   *
   * @param config - Full resolved provider config.
   * @throws {SandboxRouterError}          On router / Kubernetes API errors.
   * @throws {SandboxStartupTimeoutError}  When the sandbox doesn't become ready.
   * @throws {TemplateNotFoundError}       When the template doesn't exist.
   */
  static async create(
    config: KubernetesProviderConfig,
    _extraLabels?: Record<string, string>,
    _extraAnnotations?: Record<string, string>
  ): Promise<AgentSandboxBackend> {
    const templateName = config.templateName!;
    const client = buildClient(config);

    const info: SandboxInfo = await client.createSandbox(templateName);
    return new AgentSandboxBackend(info.name, client, config);
  }

  /**
   * Reconnect to an existing sandbox by name.
   *
   * @param sandboxName - The existing sandbox/claim name.
   * @param config      - Full resolved provider config.
   */
  static async reconnect(
    sandboxName: string,
    config: KubernetesProviderConfig
  ): Promise<AgentSandboxBackend> {
    const client = buildClient(config);
    return new AgentSandboxBackend(sandboxName, client, config);
  }

  /**
   * List all SandboxClaims in the configured namespace.
   *
   * @param config - Full resolved provider config.
   */
  static async list(config: KubernetesProviderConfig): Promise<SandboxInfo[]> {
    const client = buildClient(config);
    return client.listSandboxes();
  }

  /**
   * Delete a SandboxClaim by name.
   *
   * @param sandboxName - The sandbox/claim name to delete.
   * @param config      - Full resolved provider config.
   */
  static async deleteSandbox(
    sandboxName: string,
    config: KubernetesProviderConfig
  ): Promise<void> {
    const client = buildClient(config);
    await client.deleteSandbox(sandboxName);
  }

  // ── KubernetesBackend implementation ────────────────────────────────────────

  /**
   * Execute a shell command inside the sandbox.
   *
   * The combined stdout+stderr is returned as `output`.
   */
  async execute(
    command: string,
    options?: { timeout?: number }
  ): Promise<ExecuteResponse> {
    const timeoutMs = options?.timeout
      ? options.timeout * 1000
      : (this.config.executeTimeoutMs ?? 300_000);

    const result = await this.client.run(this.sandboxName, command, timeoutMs);
    const output = result.stdout + result.stderr;

    return {
      output,
      exitCode: result.exitCode,
      truncated: false,
    };
  }

  /**
   * Upload files into the sandbox using execute() + base64 encoding.
   * Handles arbitrary paths — creates parent directories automatically.
   */
  async uploadFiles(
    files: Array<[string, Uint8Array]>
  ): Promise<FileUploadResponse[]> {
    const results: FileUploadResponse[] = [];
    for (const [path, content] of files) {
      try {
        await this.client.uploadFile(this.sandboxName, path, content);
        results.push({ path, error: null });
      } catch (err: unknown) {
        if (err instanceof SandboxRouterError) {
          results.push({ path, error: "file_not_found" });
        } else {
          results.push({ path, error: "permission_denied" });
        }
      }
    }
    return results;
  }

  /**
   * Download files from the sandbox using execute() + base64 encoding.
   */
  async downloadFiles(paths: string[]): Promise<FileDownloadResponse[]> {
    const results: FileDownloadResponse[] = [];
    for (const path of paths) {
      try {
        const content = await this.client.downloadFile(this.sandboxName, path);
        results.push({ path, content, error: null });
      } catch {
        results.push({ path, content: null, error: "file_not_found" });
      }
    }
    return results;
  }

  /** Delete the SandboxClaim, triggering sandbox teardown. */
  async cleanup(): Promise<void> {
    await this.client.deleteSandbox(this.sandboxName);
  }
}

// ── Private helpers ────────────────────────────────────────────────────────────

/** Build a SandboxRouterClient from the provider config. */
function buildClient(config: KubernetesProviderConfig): SandboxRouterClient {
  return new SandboxRouterClient(config.routerUrl!, {
    namespace: config.namespace,
    serverPort: config.serverPort ?? 8888,
    kubeApiUrl: config.kubeApiUrl,
    kubeToken: config.kubeToken,
    sandboxReadyTimeoutMs: config.startupTimeoutMs ?? 180_000,
  });
}
