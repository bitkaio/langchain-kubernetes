import {
  BaseSandbox,
  type ExecuteResponse,
  type FileUploadResponse,
  type FileDownloadResponse,
} from "deepagents";
import type { KubernetesBackend } from "./backends/types.js";

/**
 * A sandbox backed by a Kubernetes-managed execution environment.
 *
 * `KubernetesSandbox` extends `BaseSandbox` from the `deepagents` package
 * and delegates all operations to an underlying `KubernetesBackend`:
 *
 * - **`agent-sandbox` mode** — backend talks to the sandbox-router HTTP gateway.
 * - **`raw` mode** — backend manages Pods directly via `@kubernetes/client-node`.
 *
 * The agent never needs to know which mode is active; the API is identical.
 */
export class KubernetesSandbox extends BaseSandbox {
  /** Unique sandbox identifier. */
  readonly id: string;

  private readonly backend: KubernetesBackend;
  private activityCallback?: () => void;

  constructor(backend: KubernetesBackend) {
    super();
    this.backend = backend;
    this.id = backend.id;
  }

  /**
   * Register a callback invoked (fire-and-forget) after each successful execute().
   * Used by the provider to update idle-TTL annotations without blocking execution.
   *
   * @param cb - Callback function (errors are swallowed).
   */
  setActivityCallback(cb: () => void): void {
    this.activityCallback = cb;
  }

  /**
   * Execute a shell command inside the sandbox.
   *
   * @param command - The shell command to run.
   * @param options - Optional per-call overrides (e.g., timeout in seconds).
   */
  async execute(
    command: string,
    options?: { timeout?: number }
  ): Promise<ExecuteResponse> {
    const result = await this.backend.execute(command, options);
    if (this.activityCallback) {
      try {
        this.activityCallback();
      } catch {
        // fire-and-forget — swallow errors
      }
    }
    return result;
  }

  /**
   * Upload files into the sandbox.
   *
   * @param files - Array of [absolute path inside container, file bytes] tuples.
   */
  async uploadFiles(
    files: Array<[string, Uint8Array]>
  ): Promise<FileUploadResponse[]> {
    return this.backend.uploadFiles(files);
  }

  /**
   * Download files from the sandbox.
   *
   * @param paths - Absolute paths inside the container to download.
   */
  async downloadFiles(paths: string[]): Promise<FileDownloadResponse[]> {
    return this.backend.downloadFiles(paths);
  }
}
