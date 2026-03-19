import type * as k8s from "@kubernetes/client-node";

/**
 * Configuration for the exec transport layer (raw mode only).
 */
export interface ExecuteConfig {
  /** Container name to exec into. Default: "sandbox" */
  container: string;
  /** Maximum seconds a single command may run. Default: 300 */
  timeoutSeconds: number;
  /** Maximum combined stdout+stderr bytes before truncation. Default: 1_000_000 */
  outputLimitBytes: number;
  /** Shell binary to use for command execution. Default: "/bin/sh" */
  shell: string;
}

/**
 * Full configuration for KubernetesProvider.
 *
 * All fields have sensible defaults; pass a `Partial<KubernetesProviderConfig>`
 * to the constructor and use {@link resolveConfig} to fill in the rest.
 */
export interface KubernetesProviderConfig {
  // ── Mode ──────────────────────────────────────────────────────────────────

  /**
   * Backend mode to use.
   * - `"agent-sandbox"` (default): Uses the `kubernetes-sigs/agent-sandbox`
   *   sandbox-router HTTP gateway. Requires the controller + CRDs installed.
   * - `"raw"`: Directly manages Pods via `@kubernetes/client-node`. Works on
   *   any cluster with no CRD installation. Requires `@kubernetes/client-node`
   *   and `tar-stream` to be installed.
   */
  mode?: "agent-sandbox" | "raw";

  // ── Shared ────────────────────────────────────────────────────────────────

  /**
   * Kubernetes namespace for sandbox resources.
   * Default: `"default"` in agent-sandbox mode, `"deepagents-sandboxes"` in raw mode.
   */
  namespace: string;

  /** Milliseconds to wait for the sandbox to become ready. Default: 120_000 */
  startupTimeoutMs: number;

  /** Default milliseconds for a single execute() call. Default: 300_000 */
  executeTimeoutMs: number;

  // ── agent-sandbox mode ────────────────────────────────────────────────────

  /**
   * URL of the sandbox-router service.
   * **Required** for `agent-sandbox` mode.
   *
   * Examples:
   * - In-cluster: `http://sandbox-router-svc.default.svc.cluster.local:8080`
   * - Local dev (after `kubectl port-forward svc/sandbox-router-svc 8080:8080`):
   *   `http://localhost:8080`
   */
  routerUrl?: string;

  /**
   * Name of the `SandboxTemplate` to instantiate.
   * **Required** for `agent-sandbox` mode.
   */
  templateName?: string;

  /**
   * Port the sandbox runtime server listens on inside the Pod.
   * Sent as the `X-Sandbox-Port` header to the router. Default: 8888.
   */
  serverPort?: number;

  /**
   * Kubernetes API server URL for CRD management (SandboxClaim lifecycle).
   * Defaults to the in-cluster URL (`https://kubernetes.default.svc.cluster.local`).
   * For local development, point to `kubectl proxy`: `http://localhost:8001`.
   */
  kubeApiUrl?: string;

  /**
   * Bearer token for authenticating with the Kubernetes API.
   * If omitted, the client auto-reads the in-cluster service account token
   * from `/var/run/secrets/kubernetes.io/serviceaccount/token`.
   */
  kubeToken?: string;

  // ── raw mode ──────────────────────────────────────────────────────────────

  /** Path to kubeconfig file. `undefined` = in-cluster or KUBECONFIG env var. */
  kubeconfigPath?: string;

  /** Kubeconfig context to activate. `undefined` = current context. */
  context?: string;

  /** Namespace for all sandbox Pods. Default: "deepagents-sandboxes" */
  // (overrides the shared `namespace` default for raw mode)

  /**
   * When true, each sandbox gets its own namespace for maximum isolation.
   * Default: false
   */
  namespacePerSandbox: boolean;

  /** Extra labels applied to created namespaces. */
  namespaceLabels?: Record<string, string>;

  /** Container image for the sandbox Pod. Default: "python:3.12-slim" */
  image: string;

  /** Image pull policy. Default: "IfNotPresent" */
  imagePullPolicy: "Always" | "IfNotPresent" | "Never";

  /** Names of imagePullSecrets to attach to the Pod. */
  imagePullSecrets?: string[];

  /** Working directory inside the container. Default: "/workspace" */
  workdir: string;

  /** Entrypoint command. Default: ["sleep", "infinity"] */
  command: string[];

  /** Additional environment variables injected into the sandbox container. */
  env?: Record<string, string>;

  /** ServiceAccount name to bind. `undefined` = no service account binding. */
  serviceAccount?: string;

  // ── Resources (raw mode) ──────────────────────────────────────────────────

  /** CPU request. Default: "100m" */
  cpuRequest: string;

  /** CPU limit. Default: "1000m" */
  cpuLimit: string;

  /** Memory request. Default: "256Mi" */
  memoryRequest: string;

  /** Memory limit. Default: "1Gi" */
  memoryLimit: string;

  /** Ephemeral storage limit. Default: "5Gi" */
  ephemeralStorageLimit: string;

  // ── Security (raw mode) ───────────────────────────────────────────────────

  /**
   * When true, a deny-all NetworkPolicy is created for each sandbox Pod.
   * Default: true
   */
  blockNetwork: boolean;

  /** UID the container process runs as. Default: 1000 */
  runAsUser: number;

  /** GID the container process runs as. Default: 1000 */
  runAsGroup: number;

  /** seccompProfile.type for the Pod. Default: "RuntimeDefault" */
  seccompProfile: string;

  // ── Labels and lifecycle ──────────────────────────────────────────────────

  /**
   * Labels applied to every sandbox created by this provider.
   * Keys are automatically prefixed with `langchain-kubernetes.bitkaio.com/`.
   */
  defaultLabels?: Record<string, string>;

  /**
   * Default absolute TTL from creation, in seconds.
   * Per-call overrides on `getOrCreate()` take precedence.
   */
  ttlSeconds?: number;

  /**
   * Default idle TTL from the last `execute()` call, in seconds.
   * Per-call overrides on `getOrCreate()` take precedence.
   */
  ttlIdleSeconds?: number;

  // ── Warm pool (agent-sandbox mode) ────────────────────────────────────────

  /**
   * Name of a `SandboxWarmPool` resource to claim from (agent-sandbox only).
   */
  warmPoolName?: string;

  // ── Warm pool (raw mode) ──────────────────────────────────────────────────

  /**
   * Number of warm Pods to pre-create (raw mode only).
   * `0` disables the warm pool.
   */
  warmPoolSize?: number;

  // ── Lifecycle ─────────────────────────────────────────────────────────────

  /** @deprecated Use startupTimeoutMs instead (kept for raw-mode compat). */
  startupTimeoutSeconds: number;

  /**
   * Optional: seconds after which the Pod should self-terminate.
   * Default: 3600
   */
  podTtlSeconds?: number;

  // ── Advanced (raw mode) ───────────────────────────────────────────────────

  /** Node selector constraints for scheduling. */
  nodeSelector?: Record<string, string>;

  /** Tolerations for the sandbox Pod. */
  tolerations?: k8s.V1Toleration[];

  /** Additional volumes to mount into the Pod. */
  volumes?: k8s.V1Volume[];

  /** Volume mounts applied to the sandbox container. */
  volumeMounts?: k8s.V1VolumeMount[];

  /** Init containers that run before the sandbox container. */
  initContainers?: k8s.V1Container[];

  /** Low-level overrides merged into the generated PodSpec. */
  podTemplateOverrides?: Partial<k8s.V1PodSpec>;

  /** Configuration for the exec transport (raw mode only). */
  execConfig?: Partial<ExecuteConfig>;
}

/** Default values for ExecuteConfig fields. */
export const defaultExecuteConfig: ExecuteConfig = {
  container: "sandbox",
  timeoutSeconds: 300,
  outputLimitBytes: 1_000_000,
  shell: "/bin/sh",
};

/** Default values for KubernetesProviderConfig fields (raw mode). */
export const defaultConfig: KubernetesProviderConfig = {
  mode: "agent-sandbox",
  namespace: "deepagents-sandboxes",
  startupTimeoutMs: 120_000,
  executeTimeoutMs: 300_000,
  namespacePerSandbox: false,
  image: "python:3.12-slim",
  imagePullPolicy: "IfNotPresent",
  workdir: "/workspace",
  command: ["sleep", "infinity"],
  cpuRequest: "100m",
  cpuLimit: "1000m",
  memoryRequest: "256Mi",
  memoryLimit: "1Gi",
  ephemeralStorageLimit: "5Gi",
  blockNetwork: true,
  runAsUser: 1000,
  runAsGroup: 1000,
  seccompProfile: "RuntimeDefault",
  startupTimeoutSeconds: 120,
  podTtlSeconds: 3600,
};

/**
 * Merge user-supplied partial config with defaults, returning a complete config.
 * Also normalises `startupTimeoutMs` ↔ `startupTimeoutSeconds` so both remain
 * in sync for backward compatibility.
 */
export function resolveConfig(
  partial?: Partial<KubernetesProviderConfig>
): KubernetesProviderConfig {
  const cfg = { ...defaultConfig, ...partial };

  // Keep startupTimeoutSeconds and startupTimeoutMs in sync
  if (partial?.startupTimeoutMs !== undefined && partial.startupTimeoutSeconds === undefined) {
    cfg.startupTimeoutSeconds = Math.round(cfg.startupTimeoutMs / 1000);
  } else if (partial?.startupTimeoutSeconds !== undefined && partial.startupTimeoutMs === undefined) {
    cfg.startupTimeoutMs = cfg.startupTimeoutSeconds * 1000;
  }

  return cfg;
}

/**
 * Validate that required fields for the chosen mode are present.
 *
 * @throws {Error} When a required field is missing.
 */
export function validateConfig(config: KubernetesProviderConfig): void {
  const mode = config.mode ?? "agent-sandbox";

  if (mode === "agent-sandbox") {
    if (!config.routerUrl) {
      throw new Error(
        'KubernetesProvider: "routerUrl" is required when mode="agent-sandbox". ' +
        'Example: "http://sandbox-router-svc.default.svc.cluster.local:8080"'
      );
    }
    if (!config.templateName) {
      throw new Error(
        'KubernetesProvider: "templateName" is required when mode="agent-sandbox". ' +
        'Example: "python-sandbox-template"'
      );
    }
  }
}

/**
 * Merge user-supplied partial exec config with defaults.
 */
export function resolveExecuteConfig(
  partial?: Partial<ExecuteConfig>
): ExecuteConfig {
  return { ...defaultExecuteConfig, ...partial };
}
