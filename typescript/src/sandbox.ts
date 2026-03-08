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

  constructor(backend: KubernetesBackend) {
    super();
    this.backend = backend;
    this.id = backend.id;
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
    return this.backend.execute(command, options);
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
