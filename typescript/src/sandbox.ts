import * as k8s from "@kubernetes/client-node";
import {
  BaseSandbox,
  type ExecuteResponse,
  type FileUploadResponse,
  type FileDownloadResponse,
} from "deepagents";
import type { ExecuteConfig } from "./config.js";
import { resolveExecuteConfig } from "./config.js";
import { ExecTransport } from "./exec-transport.js";
import { buildSandboxId } from "./utils.js";

/** Options passed to the KubernetesSandbox constructor. */
export interface KubernetesSandboxOptions {
  /** Name of the Pod backing this sandbox. */
  podName: string;
  /** Kubernetes namespace where the Pod lives. */
  namespace: string;
  /**
   * Whether the namespace is dedicated to this sandbox.
   * Affects the sandbox ID format (see {@link buildSandboxId}).
   */
  namespacePerSandbox: boolean;
  /** Kubeconfig used to build the exec transport. */
  kubeConfig: k8s.KubeConfig;
  /** Optional overrides for exec behaviour. */
  execConfig?: Partial<ExecuteConfig>;
}

/**
 * A sandbox backed by a single Kubernetes Pod.
 *
 * `KubernetesSandbox` extends `BaseSandbox` from the `deepagents` package.
 * The only abstract method that must be implemented is `execute()` — all
 * filesystem helpers (ls, read, write, edit, glob, grep) are inherited from
 * `BaseSandbox` and are built on top of `execute()`.
 *
 * `uploadFiles()` and `downloadFiles()` override the abstract methods from
 * `BaseSandbox` to use efficient tar-over-exec transport.
 */
export class KubernetesSandbox extends BaseSandbox {
  /** Unique sandbox identifier. */
  readonly id: string;

  private readonly podName: string;
  private readonly namespace: string;
  private readonly transport: ExecTransport;

  constructor(options: KubernetesSandboxOptions) {
    super();
    this.podName = options.podName;
    this.namespace = options.namespace;
    this.id = buildSandboxId(
      options.namespace,
      options.podName,
      options.namespacePerSandbox
    );

    const execConfig = resolveExecuteConfig(options.execConfig);
    this.transport = new ExecTransport(options.kubeConfig, execConfig);
  }

  /**
   * Execute a shell command inside the sandbox container.
   *
   * Runs the command through `/bin/sh -c` (configurable via `execConfig.shell`)
   * inside the Pod. stdout and stderr are combined into `output`.
   *
   * @param command - The shell command to run.
   * @param options - Optional per-call overrides (e.g., timeout in seconds).
   */
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

  /**
   * Upload files into the sandbox using tar-over-exec.
   *
   * Each tuple in `files` is `[absolutePath, content]`. Parent directories are
   * created automatically by tar.
   *
   * @param files - Array of [absolute path inside container, file bytes] tuples.
   */
  async uploadFiles(files: Array<[string, Uint8Array]>): Promise<FileUploadResponse[]> {
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

  /**
   * Download files from the sandbox using tar-over-exec.
   *
   * @param paths - Absolute paths inside the container to download.
   */
  async downloadFiles(paths: string[]): Promise<FileDownloadResponse[]> {
    try {
      const downloaded = await this.transport.downloadFiles(
        this.namespace,
        this.podName,
        paths
      );
      const downloadedMap = new Map(downloaded.map((f) => [f.path, f.content]));

      return paths.map((path) => {
        const content = downloadedMap.get(path);
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
}
