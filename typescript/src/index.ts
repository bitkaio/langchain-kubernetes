/**
 * langchain-kubernetes — Kubernetes sandbox provider for the DeepAgents framework.
 *
 * @example
 * ```typescript
 * import { KubernetesProvider } from "langchain-kubernetes";
 *
 * const provider = new KubernetesProvider({ image: "python:3.12-slim" });
 * const sandbox  = await provider.getOrCreate();
 *
 * const result = await sandbox.execute("python3 -c 'print(42)'");
 * console.log(result.output); // "42\n"
 *
 * await provider.delete(sandbox.id);
 * ```
 */

export { KubernetesSandbox } from "./sandbox.js";
export type { KubernetesSandboxOptions } from "./sandbox.js";

export { KubernetesProvider } from "./provider.js";
export type { SandboxInfo, SandboxListResponse } from "./provider.js";

export type {
  KubernetesProviderConfig,
  ExecuteConfig,
} from "./config.js";
export { defaultConfig, defaultExecuteConfig, resolveConfig, resolveExecuteConfig } from "./config.js";

export {
  SandboxError,
  SandboxNotFoundError,
  SandboxStartupTimeoutError,
  SandboxExecError,
  NamespaceConflictError,
} from "./errors.js";
