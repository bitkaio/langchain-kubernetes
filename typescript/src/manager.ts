import { randomUUID } from "node:crypto";
import type { KubernetesProviderConfig } from "./config.js";
import { KubernetesProvider } from "./provider.js";
import { KubernetesSandbox } from "./sandbox.js";

// ── Types ──────────────────────────────────────────────────────────────────────

/** LangGraph / LangChain RunnableConfig shape (duck-typed). */
type RunnableConfig = {
  configurable?: Record<string, unknown>;
  [key: string]: unknown;
};

/** Options for KubernetesSandboxManager. */
export interface KubernetesSandboxManagerOptions {
  /** Absolute TTL from creation (seconds). Passed to every getOrCreate() call. */
  ttlSeconds?: number;
  /** Idle TTL from last execute() (seconds). Passed to every getOrCreate() call. */
  ttlIdleSeconds?: number;
  /** Labels applied to every sandbox created by this manager (keys are auto-prefixed). */
  defaultLabels?: Record<string, string>;
}

// ── Manager ────────────────────────────────────────────────────────────────────

/**
 * High-level sandbox manager with a LangGraph-compatible `backendFactory`.
 *
 * Wraps a {@link KubernetesProvider} and maintains an in-process cache of
 * sandboxes keyed by `thread_id`. Intended for use with LangGraph's
 * `InMemorySandboxedExecutor` or any framework that needs a factory callable.
 *
 * @example LangGraph integration
 * ```typescript
 * import { KubernetesSandboxManager } from "@bitkaio/langchain-kubernetes";
 *
 * const manager = new KubernetesSandboxManager(
 *   { mode: "agent-sandbox", routerUrl: "http://...", templateName: "python" },
 *   { ttlIdleSeconds: 1800 }
 * );
 *
 * // Pass to LangGraph as the executor factory:
 * const executor = new SandboxedExecutor({ backendFactory: manager.backendFactory });
 *
 * // Cleanup on shutdown:
 * await manager.shutdown();
 * ```
 */
export class KubernetesSandboxManager {
  /** @internal */
  readonly _provider: KubernetesProvider;

  private readonly ttlSeconds?: number;
  private readonly ttlIdleSeconds?: number;
  private readonly defaultLabels?: Record<string, string>;
  /** thread_id → KubernetesSandbox */
  private readonly _cache = new Map<string, KubernetesSandbox>();
  /**
   * In-flight provisions keyed by thread_id. Concurrent callers for the same
   * thread_id all await the same Promise instead of each starting their own
   * getOrCreate — preventing N orphaned sandboxes for a single thread.
   */
  private readonly _pending = new Map<string, Promise<KubernetesSandbox>>();

  constructor(
    config: Partial<KubernetesProviderConfig>,
    options: KubernetesSandboxManagerOptions = {}
  ) {
    this._provider = new KubernetesProvider(config);
    this.ttlSeconds = options.ttlSeconds;
    this.ttlIdleSeconds = options.ttlIdleSeconds;
    this.defaultLabels = options.defaultLabels;
  }

  // ── Synchronous factory (for LangGraph) ─────────────────────────────────────

  /**
   * Returns a synchronous factory function suitable for use with LangGraph's
   * `InMemorySandboxedExecutor`.
   *
   * The factory extracts `thread_id` from the LangGraph config's
   * `configurable` field, hits the provider, and caches the result.
   *
   * @returns `(config: RunnableConfig) => Promise<KubernetesSandbox>`
   */
  get backendFactory(): (config: unknown) => Promise<KubernetesSandbox> {
    return (config: unknown) => this.abackendFactory(config);
  }

  // ── Async factory ──────────────────────────────────────────────────────────

  /**
   * Resolve a sandbox for the given LangGraph/LangChain config.
   *
   * Extracts `thread_id` from `config.configurable.thread_id`. If missing,
   * generates a UUID and logs a warning.
   *
   * Results are cached in-process; the same `thread_id` always returns the
   * same sandbox instance within a manager's lifetime.
   *
   * @param config - A LangGraph `RunnableConfig` dict or compatible object.
   */
  async abackendFactory(config: unknown): Promise<KubernetesSandbox> {
    const threadId = extractThreadId(config);

    // Fast path: already provisioned.
    const cached = this._cache.get(threadId);
    if (cached) return cached;

    // Serialise concurrent provisioning for the same thread_id.
    // If another caller is already provisioning this thread, await its result
    // rather than starting a second getOrCreate — which would create a second
    // orphaned sandbox (the same race seen with the Python LangGraph server).
    const pending = this._pending.get(threadId);
    if (pending) return pending;

    const provision = this._provider
      .getOrCreate({
        threadId,
        ttlSeconds: this.ttlSeconds,
        ttlIdleSeconds: this.ttlIdleSeconds,
        labels: this.defaultLabels,
      })
      .then((sandbox) => {
        this._cache.set(threadId, sandbox);
        this._pending.delete(threadId);
        return sandbox;
      })
      .catch((err: unknown) => {
        this._pending.delete(threadId);
        throw err;
      });

    this._pending.set(threadId, provision);
    return provision;
  }

  // ── Lookup ─────────────────────────────────────────────────────────────────

  /**
   * Return the cached sandbox for a thread ID, or `undefined` if not found.
   *
   * @param threadId - Thread/conversation identifier.
   */
  getSandbox(threadId: string): KubernetesSandbox | undefined {
    return this._cache.get(threadId);
  }

  // ── Shutdown ───────────────────────────────────────────────────────────────

  /**
   * Delete all managed sandboxes and clear the cache.
   *
   * Errors during individual deletes are logged but do not abort the loop.
   */
  async shutdown(): Promise<void> {
    const entries = Array.from(this._cache.entries());
    this._cache.clear();
    this._pending.clear();

    for (const [, sandbox] of entries) {
      try {
        await this._provider.delete(sandbox.id);
      } catch (err: unknown) {
        console.warn(
          `[langchain-kubernetes] KubernetesSandboxManager.shutdown: failed to delete ${sandbox.id}: ${String(err)}`
        );
      }
    }
  }

  // ── Context manager (async using) ─────────────────────────────────────────

  /**
   * Async dispose — called automatically when used with `await using`.
   */
  async [Symbol.asyncDispose](): Promise<void> {
    await this.shutdown();
  }
}

// ── Private helpers ────────────────────────────────────────────────────────────

/**
 * Extract `thread_id` from a LangGraph RunnableConfig.
 *
 * Accepts both plain objects and objects with a `configurable` property.
 * Generates a UUID and logs a warning if no `thread_id` is found.
 *
 * @internal
 */
export function extractThreadId(config: unknown): string {
  if (config && typeof config === "object") {
    const cfg = config as RunnableConfig;

    // Plain dict: { configurable: { thread_id: "..." } }
    if (cfg.configurable && typeof cfg.configurable === "object") {
      const tid = cfg.configurable["thread_id"];
      if (typeof tid === "string" && tid) return tid;
    }
  }

  const generated = randomUUID();
  console.warn(
    `[langchain-kubernetes] No thread_id found in config — generated UUID: ${generated}. ` +
      'Pass { configurable: { thread_id: "..." } } to associate sandboxes with conversations.'
  );
  return generated;
}
