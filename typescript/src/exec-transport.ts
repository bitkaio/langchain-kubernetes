import * as k8s from "@kubernetes/client-node";
import { Writable, Readable, PassThrough } from "node:stream";
import * as tarStream from "tar-stream";
import type { ExecuteConfig } from "./config.js";
import { SandboxExecError } from "./errors.js";

/** Return type of a successful exec invocation. */
export interface ExecResult {
  output: string;
  exitCode: number;
  truncated: boolean;
}

/** A single file entry returned by downloadFiles. */
export interface DownloadedFile {
  path: string;
  content: Uint8Array;
}

/**
 * Collect bytes from a Writable into a Buffer, honouring an optional limit.
 * Returns { data, truncated }.
 */
function makeCollector(limitBytes: number): {
  stream: Writable;
  getData: () => Buffer;
  isTruncated: () => boolean;
} {
  const chunks: Buffer[] = [];
  let total = 0;
  let truncated = false;

  const stream = new Writable({
    write(chunk: Buffer, _encoding, callback) {
      if (!truncated) {
        const available = limitBytes - total;
        if (available > 0) {
          const slice = chunk.length <= available ? chunk : chunk.subarray(0, available);
          chunks.push(slice);
          total += slice.length;
          if (total >= limitBytes) truncated = true;
        } else {
          truncated = true;
        }
      }
      callback();
    },
  });

  return {
    stream,
    getData: () => Buffer.concat(chunks),
    isTruncated: () => truncated,
  };
}

/**
 * Exec transport layer wrapping `@kubernetes/client-node`'s Exec class.
 * Handles command execution and tar-based file transfer.
 */
export class ExecTransport {
  private readonly exec: k8s.Exec;
  private readonly config: ExecuteConfig;

  constructor(kubeConfig: k8s.KubeConfig, config: ExecuteConfig) {
    this.exec = new k8s.Exec(kubeConfig);
    this.config = config;
  }

  /**
   * Run a shell command inside the given Pod/container.
   * Combines stdout and stderr into `output`. Honours timeout and output limit.
   */
  async runCommand(
    namespace: string,
    podName: string,
    command: string,
    timeoutOverrideSeconds?: number
  ): Promise<ExecResult> {
    const { container, timeoutSeconds, outputLimitBytes, shell } = this.config;
    const effectiveTimeout = (timeoutOverrideSeconds ?? timeoutSeconds) * 1000;

    const outCollector = makeCollector(outputLimitBytes);
    const errCollector = makeCollector(outputLimitBytes);

    // We capture exit code via the status channel (channel 3 in the k8s websocket protocol).
    // The @kubernetes/client-node Exec class resolves with a V1Status when the command finishes.
    let exitCode = 0;
    let timedOut = false;

    const execPromise = (async () => {
      const status = await new Promise<k8s.V1Status>((resolve, reject) => {
        this.exec
          .exec(
            namespace,
            podName,
            container,
            [shell, "-c", command],
            outCollector.stream,
            errCollector.stream,
            null, // stdin
            false, // tty
            (status: k8s.V1Status) => resolve(status)
          )
          .catch(reject);
      });

      // Parse exit code from status
      if (status.status === "Success") {
        exitCode = 0;
      } else {
        // status.details.causes contains the exit code
        const cause = status.details?.causes?.find(
          (c) => c.reason === "ExitCode"
        );
        exitCode = cause?.message ? parseInt(cause.message, 10) : 1;
      }
    })();

    const timeoutPromise = new Promise<void>((resolve) => {
      setTimeout(() => {
        timedOut = true;
        resolve();
      }, effectiveTimeout);
    });

    await Promise.race([execPromise, timeoutPromise]);

    // Combine stdout and stderr in output
    const stdoutStr = outCollector.getData().toString("utf8");
    const stderrStr = errCollector.getData().toString("utf8");
    const output = stdoutStr + stderrStr;
    const truncated = outCollector.isTruncated() || errCollector.isTruncated();

    if (timedOut) {
      return {
        output: `Command timed out after ${timeoutSeconds}s\n${output}`,
        exitCode: -1,
        truncated,
      };
    }

    return { output, exitCode, truncated };
  }

  /**
   * Upload files into the Pod via `tar xf - -C /`.
   * Builds an in-memory tar archive and streams it to the container's stdin.
   *
   * @param namespace - Pod namespace.
   * @param podName   - Pod name.
   * @param files     - Array of [absolute-path, content] tuples.
   */
  async uploadFiles(
    namespace: string,
    podName: string,
    files: Array<[string, Uint8Array]>
  ): Promise<void> {
    const { container } = this.config;

    // Build in-memory tar archive
    const pack = tarStream.pack();

    for (const [filePath, content] of files) {
      // Strip leading slash so tar extracts relative to -C /
      const entryName = filePath.startsWith("/") ? filePath.slice(1) : filePath;
      pack.entry({ name: entryName, size: content.length }, Buffer.from(content));
    }
    pack.finalize();

    // Collect the tar bytes
    const tarChunks: Buffer[] = [];
    await new Promise<void>((resolve, reject) => {
      pack.on("data", (chunk: Buffer) => tarChunks.push(chunk));
      pack.on("end", resolve);
      pack.on("error", reject);
    });
    const tarBytes = Buffer.concat(tarChunks);

    // Create a readable stream from the tar bytes
    const stdinStream = Readable.from([tarBytes]);
    const outCollector = makeCollector(65536);
    const errCollector = makeCollector(65536);

    await new Promise<void>((resolve, reject) => {
      this.exec
        .exec(
          namespace,
          podName,
          container,
          ["/bin/sh", "-c", "tar xf - -C /"],
          outCollector.stream,
          errCollector.stream,
          stdinStream,
          false,
          (status: k8s.V1Status) => {
            if (status.status === "Success") {
              resolve();
            } else {
              const stderr = errCollector.getData().toString("utf8");
              reject(
                new SandboxExecError(
                  `tar upload failed: ${status.message ?? "unknown error"}. stderr: ${stderr}`
                )
              );
            }
          }
        )
        .catch(reject);
    });
  }

  /**
   * Download files from the Pod via `tar cf - <paths>`.
   * Streams stdout to a tar extractor and returns file contents.
   *
   * @param namespace - Pod namespace.
   * @param podName   - Pod name.
   * @param paths     - Absolute paths inside the container to download.
   */
  async downloadFiles(
    namespace: string,
    podName: string,
    paths: string[]
  ): Promise<DownloadedFile[]> {
    const { container } = this.config;

    const quotedPaths = paths.map((p) => `'${p.replace(/'/g, "'\\''")}'`).join(" ");
    const tarCmd = `tar cf - ${quotedPaths} 2>/dev/null`;

    // Collect raw tar bytes from stdout
    const tarCollector = makeCollector(50 * 1024 * 1024); // 50 MB cap
    const errCollector = makeCollector(65536);

    await new Promise<void>((resolve, reject) => {
      this.exec
        .exec(
          namespace,
          podName,
          container,
          ["/bin/sh", "-c", tarCmd],
          tarCollector.stream,
          errCollector.stream,
          null,
          false,
          (status: k8s.V1Status) => {
            // tar exits non-zero if some files are missing; we still process what we got.
            void status;
            resolve();
          }
        )
        .catch(reject);
    });

    const tarData = tarCollector.getData();
    if (tarData.length === 0) return [];

    // Parse tar archive
    const results: DownloadedFile[] = [];
    const extract = tarStream.extract();

    await new Promise<void>((resolve, reject) => {
      extract.on(
        "entry",
        (
          header: tarStream.Headers,
          stream: Readable,
          next: () => void
        ) => {
          if (header.type !== "file") {
            stream.resume();
            next();
            return;
          }
          const chunks: Buffer[] = [];
          stream.on("data", (chunk: Buffer) => chunks.push(chunk));
          stream.on("end", () => {
            // Restore absolute path
            const entryPath = header.name.startsWith("/")
              ? header.name
              : `/${header.name}`;
            results.push({
              path: entryPath,
              content: Buffer.concat(chunks),
            });
            next();
          });
          stream.on("error", reject);
        }
      );
      extract.on("finish", resolve);
      extract.on("error", reject);

      // Feed the tar data into the extractor
      const readable = new PassThrough();
      readable.end(tarData);
      readable.pipe(extract);
    });

    return results;
  }
}
