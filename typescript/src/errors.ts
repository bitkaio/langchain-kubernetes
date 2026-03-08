/**
 * Base class for all errors thrown by the langchain-kubernetes package.
 */
export class SandboxError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "SandboxError";
  }
}

/**
 * Thrown when a sandbox with the given ID cannot be found in the cluster.
 */
export class SandboxNotFoundError extends SandboxError {
  constructor(sandboxId: string, options?: ErrorOptions) {
    super(`Sandbox not found: ${sandboxId}`, options);
    this.name = "SandboxNotFoundError";
  }
}

/**
 * Thrown when the sandbox Pod does not reach the Running phase within the
 * configured startup timeout.
 */
export class SandboxStartupTimeoutError extends SandboxError {
  constructor(sandboxId: string, timeoutSeconds: number, options?: ErrorOptions) {
    super(
      `Sandbox ${sandboxId} did not start within ${timeoutSeconds}s`,
      options
    );
    this.name = "SandboxStartupTimeoutError";
  }
}

/**
 * Thrown when the exec transport encounters an unrecoverable error (e.g., the
 * WebSocket closes unexpectedly before the command finishes).
 */
export class SandboxExecError extends SandboxError {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "SandboxExecError";
  }
}

/**
 * Thrown when a namespace already exists but is not managed by DeepAgents.
 */
export class NamespaceConflictError extends SandboxError {
  constructor(namespaceName: string, options?: ErrorOptions) {
    super(
      `Namespace "${namespaceName}" already exists but is not managed by DeepAgents`,
      options
    );
    this.name = "NamespaceConflictError";
  }
}
