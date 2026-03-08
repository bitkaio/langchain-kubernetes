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
 * Thrown when the sandbox does not become ready within the configured timeout.
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

/**
 * Thrown when the sandbox-router or Kubernetes API is not reachable.
 */
export class SandboxRouterError extends SandboxError {
  constructor(message: string, readonly url: string, options?: ErrorOptions) {
    super(`${message} (url: ${url})`, options);
    this.name = "SandboxRouterError";
  }
}

/**
 * Thrown when the requested SandboxTemplate does not exist.
 */
export class TemplateNotFoundError extends SandboxError {
  constructor(templateName: string, namespace: string, options?: ErrorOptions) {
    super(
      `SandboxTemplate "${templateName}" not found in namespace "${namespace}"`,
      options
    );
    this.name = "TemplateNotFoundError";
  }
}

/**
 * Thrown when a required optional dependency is not installed.
 */
export class MissingDependencyError extends SandboxError {
  constructor(mode: string, installCommand: string, options?: ErrorOptions) {
    super(
      `Missing dependencies for ${mode} mode. Install with: ${installCommand}`,
      options
    );
    this.name = "MissingDependencyError";
  }
}
