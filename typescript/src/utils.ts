import { randomBytes } from "node:crypto";

// ── Label constants ───────────────────────────────────────────────────────────

export const LABEL_MANAGED_BY = "app.kubernetes.io/managed-by";
export const LABEL_COMPONENT = "app.kubernetes.io/component";
export const LABEL_SANDBOX_ID = "deepagents.langchain.com/sandbox-id";

export const MANAGED_BY_VALUE = "deepagents";
export const COMPONENT_VALUE = "sandbox";

/** Standard labels that MUST be applied to every created K8s resource. */
export function sandboxLabels(sandboxId: string): Record<string, string> {
  return {
    [LABEL_MANAGED_BY]: MANAGED_BY_VALUE,
    [LABEL_COMPONENT]: COMPONENT_VALUE,
    [LABEL_SANDBOX_ID]: sandboxId,
  };
}

/** Label selector string used to list all DeepAgents sandboxes. */
export const MANAGED_SELECTOR = `${LABEL_MANAGED_BY}=${MANAGED_BY_VALUE},${LABEL_COMPONENT}=${COMPONENT_VALUE}`;

// ── ID generation ─────────────────────────────────────────────────────────────

/**
 * Generate a short random hex suffix suitable for K8s resource names.
 * Result is lowercase hex, 8 chars (4 bytes).
 */
export function generateSuffix(): string {
  return randomBytes(4).toString("hex");
}

/**
 * Generate a full pod name for a new sandbox.
 * Conforms to K8s naming rules: lowercase alphanumeric and hyphens.
 */
export function generatePodName(): string {
  return `deepagents-sandbox-${generateSuffix()}`;
}

/**
 * Generate a namespace name for a per-sandbox namespace strategy.
 */
export function generateNamespaceName(): string {
  return `deepagents-sandbox-${generateSuffix()}`;
}

/**
 * Build the sandbox ID string.
 * - namespacePerSandbox=false → podName only
 * - namespacePerSandbox=true  → "namespace/podName"
 */
export function buildSandboxId(
  namespace: string,
  podName: string,
  namespacePerSandbox: boolean
): string {
  return namespacePerSandbox ? `${namespace}/${podName}` : podName;
}

/**
 * Parse a sandbox ID back into namespace + pod name components.
 * Requires the shared namespace as a fallback when namespacePerSandbox=false.
 */
export function parseSandboxId(
  sandboxId: string,
  sharedNamespace: string
): { namespace: string; podName: string } {
  if (sandboxId.includes("/")) {
    const [namespace, podName] = sandboxId.split("/", 2);
    return { namespace, podName };
  }
  return { namespace: sharedNamespace, podName: sandboxId };
}

// ── Polling helpers ───────────────────────────────────────────────────────────

/** Wait for `ms` milliseconds. */
export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Poll `fn` every `intervalMs` until it returns a truthy value or `timeoutMs`
 * elapses. Returns the truthy value, or throws an Error on timeout.
 */
export async function pollUntil<T>(
  fn: () => Promise<T | undefined | null | false>,
  intervalMs: number,
  timeoutMs: number,
  timeoutMessage: string
): Promise<T> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const result = await fn();
    if (result) return result as T;
    await sleep(intervalMs);
  }
  throw new Error(timeoutMessage);
}

// ── NetworkPolicy name helper ─────────────────────────────────────────────────

/**
 * Derive the NetworkPolicy name from a sandbox ID.
 * Replaces "/" with "-" so both strategies produce valid K8s names.
 */
export function networkPolicyName(sandboxId: string): string {
  const safeSuffix = sandboxId.replace(/\//g, "-");
  return `deepagents-sandbox-deny-all-${safeSuffix}`;
}
