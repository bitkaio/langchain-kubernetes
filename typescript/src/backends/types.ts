import type {
  ExecuteResponse,
  FileUploadResponse,
  FileDownloadResponse,
} from "deepagents";

/**
 * Common interface implemented by all Kubernetes sandbox backends.
 *
 * `KubernetesSandbox` delegates every operation to whichever backend
 * is currently active (agent-sandbox or raw).
 */
export interface KubernetesBackend {
  /** Unique sandbox identifier. */
  readonly id: string;

  /**
   * Execute a shell command inside the sandbox.
   *
   * @param command - The shell command string to run.
   * @param options - Optional per-call overrides.
   */
  execute(
    command: string,
    options?: { timeout?: number }
  ): Promise<ExecuteResponse>;

  /**
   * Upload files into the sandbox.
   *
   * @param files - Array of [absolute-path, content] tuples.
   */
  uploadFiles(files: Array<[string, Uint8Array]>): Promise<FileUploadResponse[]>;

  /**
   * Download files from the sandbox.
   *
   * @param paths - Absolute paths to download.
   */
  downloadFiles(paths: string[]): Promise<FileDownloadResponse[]>;

  /** Clean up all resources held by this backend instance. */
  cleanup(): Promise<void>;
}
