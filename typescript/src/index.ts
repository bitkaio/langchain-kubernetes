/**
 * langchain-kubernetes — Kubernetes sandbox provider for the DeepAgents framework.
 *
 * Supports two backend modes:
 * - **`agent-sandbox`** (default): Uses the `kubernetes-sigs/agent-sandbox` router.
 * - **`raw`**: Directly manages Pods via `@kubernetes/client-node`.
 *
 * @example agent-sandbox mode
 * ```typescript
 * import { KubernetesProvider } from "@bitkaio/langchain-kubernetes";
 *
 * const provider = new KubernetesProvider({
 *   mode: "agent-sandbox",
 *   routerUrl: "http://sandbox-router-svc.default.svc.cluster.local:8080",
 *   templateName: "python-sandbox-template",
 * });
 * const sandbox = await provider.getOrCreate();
 * const result  = await sandbox.execute("python3 -c 'print(42)'");
 * await provider.delete(sandbox.id);
 * ```
 *
 * @example raw mode
 * ```typescript
 * import { KubernetesProvider } from "@bitkaio/langchain-kubernetes";
 *
 * const provider = new KubernetesProvider({
 *   mode: "raw",
 *   image: "python:3.12-slim",
 * });
 * const sandbox = await provider.getOrCreate();
 * await provider.delete(sandbox.id);
 * ```
 */

export { KubernetesSandbox } from "./sandbox.js";

export { KubernetesProvider } from "./provider.js";
export type {
  SandboxInfo,
  SandboxListResponse,
  CleanupResult,
  WarmPoolStatus,
  ProviderStats,
  GetOrCreateOptions,
} from "./provider.js";

export { KubernetesSandboxManager } from "./manager.js";
export type { KubernetesSandboxManagerOptions } from "./manager.js";

export type {
  KubernetesProviderConfig,
  ExecuteConfig,
} from "./config.js";
export {
  defaultConfig,
  defaultExecuteConfig,
  resolveConfig,
  resolveExecuteConfig,
  validateConfig,
} from "./config.js";

export { SandboxRouterClient } from "./router-client.js";
export type {
  SandboxInfo as RouterSandboxInfo,
  RunResult,
  SandboxRouterClientOptions,
} from "./router-client.js";

export type { KubernetesBackend } from "./backends/types.js";

export {
  SandboxError,
  SandboxNotFoundError,
  SandboxStartupTimeoutError,
  SandboxExecError,
  NamespaceConflictError,
  SandboxRouterError,
  TemplateNotFoundError,
  MissingDependencyError,
} from "./errors.js";
