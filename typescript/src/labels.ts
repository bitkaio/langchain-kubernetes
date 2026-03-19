/**
 * Label and annotation constants and utilities for langchain-kubernetes resources.
 *
 * All provider-managed resources carry the prefix `langchain-kubernetes.bitkaio.com/`
 * on every label and annotation to avoid collisions.
 */

import { createHash } from "node:crypto";

// ── Constants ─────────────────────────────────────────────────────────────────

/** Namespace prefix for all labels and annotations managed by this provider. */
export const LABEL_PREFIX = "langchain-kubernetes.bitkaio.com/";

/** Label key identifying resources managed by this provider. */
export const LK_LABEL_MANAGED_BY = `${LABEL_PREFIX}managed-by`;

/** Label value for {@link LK_LABEL_MANAGED_BY}. */
export const LK_MANAGED_BY_VALUE = "langchain-kubernetes";

/** Label key for the thread/conversation identifier. */
export const LK_LABEL_THREAD_ID = `${LABEL_PREFIX}thread-id`;

/** Label key for warm-pool status (`"warm"` | `"active"`). */
export const LK_LABEL_POOL_STATUS = `${LABEL_PREFIX}pool-status`;

/** Annotation key: original thread_id before sanitisation (set when hashed). */
export const ANN_THREAD_ID_ORIGINAL = `${LABEL_PREFIX}thread-id-original`;

/** Annotation key: absolute TTL from creation, in seconds. */
export const ANN_TTL_SECONDS = `${LABEL_PREFIX}ttl-seconds`;

/** Annotation key: idle TTL from last execute() call, in seconds. */
export const ANN_TTL_IDLE_SECONDS = `${LABEL_PREFIX}ttl-idle-seconds`;

/** Annotation key: ISO-8601 UTC timestamp when the sandbox was created. */
export const ANN_CREATED_AT = `${LABEL_PREFIX}created-at`;

/** Annotation key: ISO-8601 UTC timestamp of the last execute() call. */
export const ANN_LAST_ACTIVITY = `${LABEL_PREFIX}last-activity`;

/** Warm-pool pod status values. */
export const POOL_STATUS_WARM = "warm";
export const POOL_STATUS_ACTIVE = "active";

/** Label selector matching all sandboxes managed by this provider. */
export const LK_MANAGED_SELECTOR = `${LK_LABEL_MANAGED_BY}=${LK_MANAGED_BY_VALUE}`;

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Pattern for valid Kubernetes label values. */
const VALID_LABEL_VALUE_RE = /^[a-zA-Z0-9]([a-zA-Z0-9._-]{0,61}[a-zA-Z0-9])?$|^[a-zA-Z0-9]$|^$/;

/**
 * Sanitize a string for use as a Kubernetes label value.
 *
 * Kubernetes label values must be ≤ 63 characters and match `[a-zA-Z0-9._-]`,
 * starting and ending with alphanumeric characters.
 *
 * If the value fails these constraints it is hashed (SHA-256, first 12 hex
 * chars) and the original is returned so the caller can store it as an annotation.
 *
 * @param value - Raw string to sanitize.
 * @returns Tuple of `[safeValue, originalIfHashed | undefined]`.
 */
export function sanitizeLabelValue(value: string): [string, string | undefined] {
  if (value === "" || (value.length <= 63 && VALID_LABEL_VALUE_RE.test(value))) {
    return [value, undefined];
  }
  const hashed = createHash("sha256").update(value).digest("hex").slice(0, 12);
  return [hashed, value];
}

/**
 * Merge labels following the priority order defined in the spec.
 *
 * Priority (later wins):
 * 1. `managed-by` (always set).
 * 2. `defaultLabels` — config-level labels, auto-prefixed.
 * 3. `callLabels` — per-call labels, auto-prefixed, override defaults.
 * 4. `threadId` — if provided.
 *
 * @returns `[labels, annotations]` where `annotations` is non-empty only when
 *   `threadId` required sanitisation.
 */
export function buildLabels(options: {
  defaultLabels?: Record<string, string>;
  callLabels?: Record<string, string>;
  threadId?: string;
}): [Record<string, string>, Record<string, string>] {
  const labels: Record<string, string> = {
    [LK_LABEL_MANAGED_BY]: LK_MANAGED_BY_VALUE,
  };
  const annotations: Record<string, string> = {};

  for (const source of [options.defaultLabels, options.callLabels]) {
    if (source) {
      for (const [k, v] of Object.entries(source)) {
        labels[`${LABEL_PREFIX}${k}`] = v;
      }
    }
  }

  if (options.threadId !== undefined) {
    const [safe, original] = sanitizeLabelValue(options.threadId);
    labels[LK_LABEL_THREAD_ID] = safe;
    if (original !== undefined) {
      annotations[ANN_THREAD_ID_ORIGINAL] = original;
    }
  }

  return [labels, annotations];
}

/**
 * Build TTL-related annotation dict.
 *
 * @param ttlSeconds - Absolute TTL from creation (seconds).
 * @param ttlIdleSeconds - Idle TTL from last execute() (seconds).
 * @returns Annotations dict.
 */
export function buildTtlAnnotations(options: {
  ttlSeconds?: number;
  ttlIdleSeconds?: number;
}): Record<string, string> {
  const annotations: Record<string, string> = {};
  if (options.ttlSeconds !== undefined) {
    annotations[ANN_TTL_SECONDS] = String(options.ttlSeconds);
    annotations[ANN_CREATED_AT] = nowIso();
  }
  if (options.ttlIdleSeconds !== undefined) {
    annotations[ANN_TTL_IDLE_SECONDS] = String(options.ttlIdleSeconds);
  }
  return annotations;
}

/**
 * Return the current UTC timestamp in ISO-8601 format.
 */
export function nowIso(): string {
  return new Date().toISOString();
}

/**
 * Build a label selector string for a specific thread_id.
 *
 * @param threadId - Raw thread identifier (will be sanitised automatically).
 * @returns Label selector string.
 */
export function threadIdSelector(threadId: string): string {
  const [safe] = sanitizeLabelValue(threadId);
  return `${LK_LABEL_MANAGED_BY}=${LK_MANAGED_BY_VALUE},${LK_LABEL_THREAD_ID}=${safe}`;
}

/**
 * Return the label selector for available warm-pool Pods.
 */
export function warmPoolSelector(): string {
  return `${LK_LABEL_MANAGED_BY}=${LK_MANAGED_BY_VALUE},${LK_LABEL_POOL_STATUS}=${POOL_STATUS_WARM}`;
}
