import { readFile } from "node:fs/promises";
import { randomBytes } from "node:crypto";
import {
  SandboxRouterError,
  SandboxStartupTimeoutError,
  TemplateNotFoundError,
} from "./errors.js";
import { pollUntil } from "./utils.js";

// ── Kubernetes CRD constants ───────────────────────────────────────────────────

const CLAIM_API_GROUP = "extensions.agents.x-k8s.io";
const CLAIM_API_VERSION = "v1alpha1";
const CLAIM_PLURAL = "sandboxclaims";

const SANDBOX_API_GROUP = "agents.x-k8s.io";
const SANDBOX_API_VERSION = "v1alpha1";
const SANDBOX_PLURAL = "sandboxes";

const IN_CLUSTER_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token";
const IN_CLUSTER_API_URL = "https://kubernetes.default.svc.cluster.local";

// ── Public types ──────────────────────────────────────────────────────────────

/** Result returned by the sandbox execute endpoint. */
export interface RunResult {
  stdout: string;
  stderr: string;
  exitCode: number;
}

/** Info about a provisioned sandbox. */
export interface SandboxInfo {
  /** The sandbox/claim name used as X-Sandbox-ID. */
  name: string;
  namespace: string;
  ready: boolean;
  phase?: string;
}

/** Options for the SandboxRouterClient constructor. */
export interface SandboxRouterClientOptions {
  /** Kubernetes namespace where SandboxClaims are created. Default: "default". */
  namespace?: string;
  /**
   * The port the sandbox runtime server listens on. Default: 8888.
   * Sent as the `X-Sandbox-Port` header.
   */
  serverPort?: number;
  /**
   * Kubernetes API server URL. Defaults to in-cluster URL
   * (`https://kubernetes.default.svc.cluster.local`).
   * For local development with `kubectl proxy`, use `http://localhost:8001`.
   */
  kubeApiUrl?: string;
  /**
   * Bearer token for authenticating with the Kubernetes API.
   * If omitted, the client reads the in-cluster service account token from
   * `/var/run/secrets/kubernetes.io/serviceaccount/token`.
   */
  kubeToken?: string;
  /**
   * Milliseconds to wait for a sandbox to become ready after creating a claim.
   * Default: 180_000 (3 minutes).
   */
  sandboxReadyTimeoutMs?: number;
}

// ── SandboxRouterClient ────────────────────────────────────────────────────────

/**
 * HTTP client for the `kubernetes-sigs/agent-sandbox` sandbox-router.
 *
 * Responsibilities:
 * - Manages the `SandboxClaim` CRD lifecycle via the Kubernetes REST API
 *   (create, delete, list, status).
 * - Proxies runtime operations (execute, upload, download) through the
 *   sandbox-router, which forwards requests to the sandbox Pod's HTTP server.
 *
 * The router proxy uses three request headers to locate the target sandbox:
 * - `X-Sandbox-ID`: the claim/sandbox name
 * - `X-Sandbox-Namespace`: the sandbox namespace
 * - `X-Sandbox-Port`: the port the runtime server listens on (default 8888)
 *
 * **In-cluster usage** (the primary mode):
 * ```typescript
 * const client = new SandboxRouterClient("http://sandbox-router-svc:8080", {
 *   namespace: "default",
 * });
 * ```
 *
 * **Local development** — run `kubectl proxy` then:
 * ```typescript
 * const client = new SandboxRouterClient("http://localhost:8080", {
 *   kubeApiUrl: "http://localhost:8001", // kubectl proxy
 * });
 * ```
 */
export class SandboxRouterClient {
  private readonly routerUrl: string;
  private readonly namespace: string;
  private readonly serverPort: number;
  private readonly kubeApiUrl: string;
  private readonly kubeTokenOverride?: string;
  private readonly sandboxReadyTimeoutMs: number;

  constructor(routerUrl: string, options?: SandboxRouterClientOptions) {
    this.routerUrl = routerUrl.replace(/\/$/, "");
    this.namespace = options?.namespace ?? "default";
    this.serverPort = options?.serverPort ?? 8888;
    this.kubeApiUrl = (options?.kubeApiUrl ?? IN_CLUSTER_API_URL).replace(/\/$/, "");
    this.kubeTokenOverride = options?.kubeToken;
    this.sandboxReadyTimeoutMs = options?.sandboxReadyTimeoutMs ?? 180_000;
  }

  // ── CRD lifecycle ──────────────────────────────────────────────────────────

  /**
   * Create a SandboxClaim for the given template and wait until the Sandbox
   * becomes ready.
   *
   * @param templateName - The `SandboxTemplate` name to instantiate.
   * @returns Info about the ready sandbox (use `name` as X-Sandbox-ID).
   * @throws {TemplateNotFoundError} When the template does not exist.
   * @throws {SandboxStartupTimeoutError} When the sandbox doesn't become ready in time.
   * @throws {SandboxRouterError} On Kubernetes API connectivity errors.
   */
  async createSandbox(templateName: string): Promise<SandboxInfo> {
    const claimName = `sandbox-claim-${randomBytes(4).toString("hex")}`;

    const manifest = {
      apiVersion: `${CLAIM_API_GROUP}/${CLAIM_API_VERSION}`,
      kind: "SandboxClaim",
      metadata: { name: claimName },
      spec: { sandboxTemplateRef: { name: templateName } },
    };

    try {
      await this.k8sRequest(
        "POST",
        `/apis/${CLAIM_API_GROUP}/${CLAIM_API_VERSION}/namespaces/${this.namespace}/${CLAIM_PLURAL}`,
        manifest
      );
    } catch (err: unknown) {
      if (isK8sNotFound(err)) {
        throw new TemplateNotFoundError(templateName, this.namespace);
      }
      throw err;
    }

    // Poll until the Sandbox with this name has Ready=True
    const sandboxInfo = await this.waitForReady(claimName);
    return sandboxInfo;
  }

  /**
   * Delete a SandboxClaim by name. Idempotent — deleting a non-existent claim
   * is a no-op.
   *
   * @param sandboxName - The claim/sandbox name to delete.
   */
  async deleteSandbox(sandboxName: string): Promise<void> {
    try {
      await this.k8sRequest(
        "DELETE",
        `/apis/${CLAIM_API_GROUP}/${CLAIM_API_VERSION}/namespaces/${this.namespace}/${CLAIM_PLURAL}/${sandboxName}`
      );
    } catch (err: unknown) {
      if (isK8sNotFound(err)) return; // Already gone
      throw err;
    }
  }

  /**
   * List all SandboxClaims in the configured namespace.
   */
  async listSandboxes(): Promise<SandboxInfo[]> {
    const resp = await this.k8sRequest<{ items: unknown[] }>(
      "GET",
      `/apis/${CLAIM_API_GROUP}/${CLAIM_API_VERSION}/namespaces/${this.namespace}/${CLAIM_PLURAL}`
    );
    return (resp.items ?? []).map((item) => claimToInfo(item, this.namespace));
  }

  /**
   * Get the current status of a sandbox.
   *
   * @param sandboxName - The claim/sandbox name.
   */
  async status(sandboxName: string): Promise<SandboxInfo> {
    try {
      const item = await this.k8sRequest<unknown>(
        "GET",
        `/apis/${SANDBOX_API_GROUP}/${SANDBOX_API_VERSION}/namespaces/${this.namespace}/${SANDBOX_PLURAL}/${sandboxName}`
      );
      return sandboxObjectToInfo(item, this.namespace);
    } catch (err: unknown) {
      if (isK8sNotFound(err)) {
        return { name: sandboxName, namespace: this.namespace, ready: false };
      }
      throw err;
    }
  }

  // ── Router proxy operations ────────────────────────────────────────────────

  /**
   * Execute a shell command inside the sandbox.
   *
   * The command is wrapped in `sh -c '...'` before being sent so that all
   * shell features (pipes, redirection, variable expansion) work correctly.
   *
   * @param sandboxName - The claim/sandbox name (X-Sandbox-ID).
   * @param command     - The shell command to run.
   * @param timeoutMs   - Optional request timeout in milliseconds.
   */
  async run(
    sandboxName: string,
    command: string,
    timeoutMs?: number
  ): Promise<RunResult> {
    // Wrap in sh -c so all shell features work (pipes, redirects, etc.)
    const wrappedCommand = `sh -c ${shellQuote(command)}`;

    const resp = await this.routerRequest<{
      stdout: string;
      stderr: string;
      exit_code: number;
    }>(
      "POST",
      "/execute",
      sandboxName,
      { command: wrappedCommand },
      timeoutMs
    );

    return {
      stdout: resp.stdout ?? "",
      stderr: resp.stderr ?? "",
      exitCode: resp.exit_code ?? 0,
    };
  }

  /**
   * Upload a file into the sandbox using `execute()` + base64 encoding.
   * This approach handles arbitrary file paths correctly.
   *
   * @param sandboxName - The claim/sandbox name.
   * @param path        - Absolute destination path inside the sandbox.
   * @param content     - File contents.
   */
  async uploadFile(
    sandboxName: string,
    path: string,
    content: Uint8Array
  ): Promise<void> {
    const b64 = Buffer.from(content).toString("base64");
    const safePath = shellQuote(path);
    const cmd = `mkdir -p $(dirname ${safePath}) && printf '%s' ${shellQuote(b64)} | base64 -d > ${safePath}`;
    const result = await this.run(sandboxName, cmd);
    if (result.exitCode !== 0) {
      throw new SandboxRouterError(
        `Upload failed for ${path}: ${result.stderr}`,
        this.routerUrl
      );
    }
  }

  /**
   * Download a file from the sandbox using `execute()` + base64 encoding.
   *
   * @param sandboxName - The claim/sandbox name.
   * @param path        - Absolute path inside the sandbox.
   */
  async downloadFile(sandboxName: string, path: string): Promise<Uint8Array> {
    const result = await this.run(sandboxName, `base64 -w 0 ${shellQuote(path)}`);
    if (result.exitCode !== 0) {
      throw new SandboxRouterError(
        `Download failed for ${path}: ${result.stderr}`,
        this.routerUrl
      );
    }
    return Buffer.from(result.stdout.trim(), "base64");
  }

  // ── Private helpers ────────────────────────────────────────────────────────

  /** Poll until a Sandbox object shows Ready=True. */
  private async waitForReady(claimName: string): Promise<SandboxInfo> {
    try {
      const result = await pollUntil(
        async () => {
          try {
            const info = await this.status(claimName);
            return info.ready ? info : null;
          } catch {
            return null;
          }
        },
        3000,
        this.sandboxReadyTimeoutMs,
        "timeout"
      );
      return result;
    } catch {
      // Clean up on timeout
      await this.deleteSandbox(claimName).catch(() => undefined);
      throw new SandboxStartupTimeoutError(
        claimName,
        Math.round(this.sandboxReadyTimeoutMs / 1000)
      );
    }
  }

  /** Build router request headers for a sandbox. */
  private routerHeaders(sandboxName: string): Record<string, string> {
    return {
      "Content-Type": "application/json",
      "X-Sandbox-ID": sandboxName,
      "X-Sandbox-Namespace": this.namespace,
      "X-Sandbox-Port": String(this.serverPort),
    };
  }

  /** Make a request through the sandbox router. */
  private async routerRequest<T>(
    method: string,
    path: string,
    sandboxName: string,
    body?: unknown,
    timeoutMs?: number
  ): Promise<T> {
    const url = `${this.routerUrl}${path}`;
    const controller = new AbortController();
    const timer = timeoutMs
      ? setTimeout(() => controller.abort(), timeoutMs)
      : undefined;

    try {
      const resp = await fetch(url, {
        method,
        headers: this.routerHeaders(sandboxName),
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });

      if (!resp.ok) {
        const text = await resp.text().catch(() => "");
        throw new SandboxRouterError(
          `Router returned ${resp.status} for ${method} ${path}: ${text}`,
          this.routerUrl
        );
      }

      return (await resp.json()) as T;
    } catch (err: unknown) {
      if (err instanceof SandboxRouterError) throw err;
      throw new SandboxRouterError(
        `Failed to reach router at ${url}: ${String(err)}`,
        this.routerUrl
      );
    } finally {
      if (timer !== undefined) clearTimeout(timer);
    }
  }

  /** Read the Kubernetes bearer token. */
  private async readToken(): Promise<string | undefined> {
    if (this.kubeTokenOverride) return this.kubeTokenOverride;
    try {
      return (await readFile(IN_CLUSTER_TOKEN_PATH, "utf8")).trim();
    } catch {
      return undefined;
    }
  }

  /** Make a request to the Kubernetes API. */
  private async k8sRequest<T>(
    method: string,
    path: string,
    body?: unknown
  ): Promise<T> {
    const token = await this.readToken();
    const url = `${this.kubeApiUrl}${path}`;

    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      Accept: "application/json",
    };
    if (token) headers["Authorization"] = `Bearer ${token}`;

    let resp: Response;
    try {
      resp = await fetch(url, {
        method,
        headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
        // Allow self-signed certs in in-cluster environments
        // Node.js 18+ supports this via env var NODE_TLS_REJECT_UNAUTHORIZED=0
        // For production, mount the CA cert and set NODE_EXTRA_CA_CERTS
      });
    } catch (err: unknown) {
      throw new SandboxRouterError(
        `Failed to reach Kubernetes API at ${url}: ${String(err)}`,
        this.kubeApiUrl
      );
    }

    if (!resp.ok) {
      const text = await resp.text().catch(() => "");
      const k8sErr = new K8sApiError(resp.status, text, method, path);
      throw k8sErr;
    }

    if (resp.status === 204) return undefined as T;

    const contentType = resp.headers.get("content-type") ?? "";
    if (!contentType.includes("application/json")) return undefined as T;

    return (await resp.json()) as T;
  }
}

// ── Internal helpers ──────────────────────────────────────────────────────────

/** Internal error class carrying the HTTP status code of a k8s API response. */
class K8sApiError extends Error {
  constructor(
    readonly statusCode: number,
    message: string,
    readonly method: string,
    readonly path: string
  ) {
    super(`Kubernetes API ${method} ${path} returned ${statusCode}: ${message}`);
    this.name = "K8sApiError";
  }
}

/** Check whether a caught error is a Kubernetes 404 Not Found. */
function isK8sNotFound(err: unknown): boolean {
  if (err instanceof K8sApiError) return err.statusCode === 404;
  return false;
}

/** Convert a raw Kubernetes claim/sandbox object to SandboxInfo. */
function claimToInfo(item: unknown, namespace: string): SandboxInfo {
  if (!item || typeof item !== "object") {
    return { name: "unknown", namespace, ready: false };
  }
  const obj = item as Record<string, unknown>;
  const meta = (obj["metadata"] ?? {}) as Record<string, unknown>;
  const name = (meta["name"] as string | undefined) ?? "unknown";
  return { name, namespace, ready: false };
}

/** Convert a raw Sandbox object with status conditions to SandboxInfo. */
function sandboxObjectToInfo(item: unknown, namespace: string): SandboxInfo {
  if (!item || typeof item !== "object") {
    return { name: "unknown", namespace, ready: false };
  }
  const obj = item as Record<string, unknown>;
  const meta = (obj["metadata"] ?? {}) as Record<string, unknown>;
  const name = (meta["name"] as string | undefined) ?? "unknown";

  const status = (obj["status"] ?? {}) as Record<string, unknown>;
  const conditions = (status["conditions"] as unknown[]) ?? [];
  const ready = conditions.some((c) => {
    if (!c || typeof c !== "object") return false;
    const cond = c as Record<string, unknown>;
    return cond["type"] === "Ready" && cond["status"] === "True";
  });

  const phase = (status["phase"] as string | undefined);

  return { name, namespace, ready, phase };
}

/**
 * Shell-quote a string using single quotes, safely escaping any embedded
 * single quotes.
 *
 * Example: `hello 'world'` → `'hello '\''world'\''`
 */
function shellQuote(s: string): string {
  return `'${s.replace(/'/g, "'\\''")}'`;
}
