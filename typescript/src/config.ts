import type * as k8s from "@kubernetes/client-node";

/**
 * Configuration for the exec transport layer within a sandbox.
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
 * All fields have sensible defaults; pass a Partial<KubernetesProviderConfig>
 * to the constructor and use {@link defaultConfig} to fill in the rest.
 */
export interface KubernetesProviderConfig {
  // ── Cluster connection ────────────────────────────────────────────────────

  /** Path to kubeconfig file. undefined = in-cluster or KUBECONFIG env var. */
  kubeconfigPath?: string;
  /** Kubeconfig context to activate. undefined = current context. */
  context?: string;

  // ── Namespace strategy ────────────────────────────────────────────────────

  /** Namespace for all sandbox Pods. Default: "deepagents-sandboxes" */
  namespace: string;
  /**
   * When true, each sandbox gets its own namespace for maximum isolation.
   * Deletion of the sandbox cascades to the entire namespace.
   * Default: false
   */
  namespacePerSandbox: boolean;
  /** Extra labels applied to created namespaces. */
  namespaceLabels?: Record<string, string>;

  // ── Pod template ──────────────────────────────────────────────────────────

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
  /** ServiceAccount name to bind. undefined = no service account binding. */
  serviceAccount?: string;

  // ── Resources ─────────────────────────────────────────────────────────────

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

  // ── Security ──────────────────────────────────────────────────────────────

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

  // ── Lifecycle ─────────────────────────────────────────────────────────────

  /** Seconds to wait for the sandbox Pod to become Running. Default: 120 */
  startupTimeoutSeconds: number;
  /**
   * Optional: seconds after which the Pod should self-terminate (via a sidecar
   * or external controller). Not enforced by this provider directly.
   * Default: 3600
   */
  podTtlSeconds?: number;

  // ── Advanced ──────────────────────────────────────────────────────────────

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

  // ── Exec config ───────────────────────────────────────────────────────────

  /** Configuration for the exec transport. */
  execConfig?: Partial<ExecuteConfig>;
}

/** Default values for ExecuteConfig fields. */
export const defaultExecuteConfig: ExecuteConfig = {
  container: "sandbox",
  timeoutSeconds: 300,
  outputLimitBytes: 1_000_000,
  shell: "/bin/sh",
};

/** Default values for KubernetesProviderConfig fields. */
export const defaultConfig: KubernetesProviderConfig = {
  namespace: "deepagents-sandboxes",
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
 */
export function resolveConfig(
  partial?: Partial<KubernetesProviderConfig>
): KubernetesProviderConfig {
  return { ...defaultConfig, ...partial };
}

/**
 * Merge user-supplied partial exec config with defaults.
 */
export function resolveExecuteConfig(
  partial?: Partial<ExecuteConfig>
): ExecuteConfig {
  return { ...defaultExecuteConfig, ...partial };
}
