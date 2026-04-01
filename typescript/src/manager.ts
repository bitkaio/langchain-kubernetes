/**
 * KubernetesSandboxManager — LangGraph-integrated sandbox lifecycle manager.
 *
 * Provides {@link KubernetesSandboxManager.createAgent} — a factory that
 * returns a compiled LangGraph graph with streaming-compatible sandbox management.
 * All sandbox state is stored in graph state and persisted by LangGraph's
 * checkpointer — no Kubernetes label writes or direct cluster API access needed.
 */

import type { KubernetesProviderConfig } from "./config.js";
import { KubernetesProvider } from "./provider.js";
import type { CleanupResult } from "./provider.js";
import { KubernetesSandbox } from "./sandbox.js";

// ── Types ───────────────────────────────────────────────────────────────────

/** Options for {@link KubernetesSandboxManager}. */
export interface KubernetesSandboxManagerOptions {
  /** Absolute TTL from creation (seconds). Passed to every getOrCreate() call. */
  ttlSeconds?: number;
  /** Idle TTL from last execute() (seconds). Passed to every getOrCreate() call. */
  ttlIdleSeconds?: number;
  /** Labels applied to every sandbox created by this manager (keys are auto-prefixed). */
  defaultLabels?: Record<string, string>;
}

/** @internal */
type AnyModel = unknown;

// ── Manager ─────────────────────────────────────────────────────────────────

/**
 * High-level sandbox manager for LangGraph applications.
 *
 * Wraps a {@link KubernetesProvider} and provides {@link createAgent} — a
 * factory that returns a compiled LangGraph graph using a two-node architecture
 * (`START → setup → agent → END`) that enables real-time streaming of LLM
 * tokens and tool calls from LangGraph Studio and the LangGraph Platform.
 *
 * The `sandboxId` lives in LangGraph graph state and is persisted by whichever
 * checkpointer the user configures (`MemorySaver`, `PostgresSaver`, …). This
 * means the integration works transparently across process restarts, horizontal
 * scaling, and the LangGraph Platform.
 *
 * @example Minimal LangGraph integration
 * ```typescript
 * import { KubernetesSandboxManager } from "@bitkaio/langchain-kubernetes";
 *
 * const manager = new KubernetesSandboxManager(
 *   {
 *     mode: "agent-sandbox",
 *     routerUrl: "http://my-gateway/sandbox-router",
 *     templateName: "python-sandbox-template",
 *   },
 *   { ttlIdleSeconds: 1800 }
 * );
 *
 * export const graph = await manager.createAgent(model);
 * ```
 */
export class KubernetesSandboxManager {
  /** @internal */
  readonly _provider: KubernetesProvider;

  /** @internal — maps thread_id → live sandbox for the current process */
  readonly _sandboxByThread = new Map<string, KubernetesSandbox>();

  private readonly ttlSeconds?: number;
  private readonly ttlIdleSeconds?: number;
  private readonly defaultLabels?: Record<string, string>;

  constructor(
    config: Partial<KubernetesProviderConfig>,
    options: KubernetesSandboxManagerOptions = {}
  ) {
    this._provider = new KubernetesProvider(config);
    this.ttlSeconds = options.ttlSeconds;
    this.ttlIdleSeconds = options.ttlIdleSeconds;
    this.defaultLabels = options.defaultLabels;
  }

  // ── Primary integration point: compiled agent factory ─────────────────────

  /**
   * Return a backend factory that reads the cached sandbox for the current
   * LangGraph thread via the `@langchain/core` AsyncLocalStorage context.
   *
   * The factory is sync (deepagents requirement). The sandbox must be
   * preloaded into `_sandboxByThread` before the factory is called —
   * `createAgent()` handles this automatically by wrapping the agent's
   * `invoke`/`stream` methods to call `_ensureSandbox()` first.
   *
   * @internal
   */
  async _makeBackendFactory(): Promise<(runtime: unknown) => KubernetesSandbox> {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const { AsyncLocalStorageProviderSingleton } = await import("@langchain/core/singletons" as any);
    const sandboxByThread = this._sandboxByThread;

    return function backendFactory(_runtime: unknown): KubernetesSandbox {
      const config = AsyncLocalStorageProviderSingleton.getInstance().getStore() as
        | Record<string, unknown>
        | undefined;
      const threadId = (config?.["configurable"] as Record<string, unknown> | undefined)?.[
        "thread_id"
      ] as string | undefined;

      if (!threadId) {
        throw new Error(
          "KubernetesSandboxManager: no thread_id in LangGraph config. " +
            "Invoke with config: { configurable: { thread_id: '...' } }."
        );
      }
      const sandbox = sandboxByThread.get(threadId);
      if (!sandbox) {
        throw new Error(
          `KubernetesSandboxManager: no sandbox cached for thread ${JSON.stringify(threadId)}. ` +
            "Ensure the sandbox is preloaded before the agent runs."
        );
      }
      return sandbox;
    };
  }

  /**
   * Ensure a sandbox is cached for the given `thread_id`.
   *
   * If one already exists in `_sandboxByThread`, this is a no-op. Otherwise
   * acquires a new sandbox via the provider and caches it.
   *
   * @internal
   */
  async _ensureSandbox(threadId: string): Promise<void> {
    if (this._sandboxByThread.has(threadId)) return;
    const sandbox = await this._getOrReconnect(undefined);
    this._sandboxByThread.set(threadId, sandbox);
  }

  /**
   * Return an async LangGraph node that acquires the sandbox and caches it.
   *
   * Reads `state[stateSandboxKey]` to reconnect an existing sandbox (or
   * provision a new one), stores it in `_sandboxByThread[thread_id]`, and
   * writes the (possibly new) `sandboxId` back to state.
   *
   * Must be wired to run before the deepagent subgraph node.
   *
   * @param stateSandboxKey - State field that holds the sandbox ID. Defaults to `"sandboxId"`.
   */
  createSetupNode(
    { stateSandboxKey = "sandboxId" }: { stateSandboxKey?: string } = {}
  ): (state: Record<string, unknown>, config?: unknown) => Promise<Record<string, unknown>> {
    const manager = this;

    return async function setupNode(
      state: Record<string, unknown>,
      config?: unknown
    ): Promise<Record<string, unknown>> {
      const cfg = config as Record<string, unknown> | undefined;
      const threadId = (cfg?.["configurable"] as Record<string, unknown> | undefined)?.[
        "thread_id"
      ] as string | undefined;

      if (!threadId) {
        throw new Error("KubernetesSandboxManager setupNode: no thread_id in config.");
      }

      const sandboxId = state[stateSandboxKey] as string | undefined;
      const sandbox = await manager._getOrReconnect(sandboxId);
      manager._sandboxByThread.set(threadId, sandbox);

      const updates: Record<string, unknown> = {};
      if (sandbox.id !== sandboxId) {
        updates[stateSandboxKey] = sandbox.id;
      }
      return updates;
    };
  }

  /**
   * Return a deepagent graph with lazy sandbox acquisition.
   *
   * The sandbox is acquired synchronously on the first tool call for a given
   * `thread_id` via `_makeBackendFactory()`. No wrapper `StateGraph` is used,
   * so all deepagent steps (todos, tool calls, LLM tokens) are emitted as
   * top-level graph events — visible in the Deep Agent UI and LangGraph
   * Platform streaming.
   *
   * @param model - A LangChain `BaseChatModel` (or compatible) passed to
   *   `createDeepAgent()`.
   * @param options.checkpointer - LangGraph checkpointer (`MemorySaver`,
   *   `PostgresSaver`, …). Required when calling `.invoke()` directly (e.g. in
   *   Express). The LangGraph Platform / `langgraph dev` provide their own
   *   checkpointer — omit it there.
   * @param options.stateSandboxKey - State field name for the sandbox ID.
   *   Accepted for API compatibility but unused — sandbox IDs live in
   *   `_sandboxByThread`, not in graph state.
   * @param options - Extra options forwarded to `createDeepAgent()`.
   *
   * @example Express — pass a checkpointer
   * ```typescript
   * const agent = await manager.createAgent(llm, { checkpointer: new MemorySaver() });
   * const result = await agent.invoke(
   *   { messages: [{ role: "user", content: "hello" }] },
   *   { configurable: { thread_id: "conv-1" } },
   * );
   * ```
   *
   * @example LangGraph Platform — no checkpointer needed
   * ```typescript
   * export const graph = await manager.createAgent(llm);
   * ```
   */
  async createAgent(
    model: AnyModel,
    {
      checkpointer,
      stateSandboxKey = "sandboxId",
      ...createDeepAgentOptions
    }: {
      checkpointer?: unknown;
      stateSandboxKey?: string;
      [key: string]: unknown;
    } = {}
  ): Promise<unknown> {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const { createDeepAgent } = await import("deepagents" as any);

    const backendFactory = await this._makeBackendFactory();
    const agent = createDeepAgent(model, {
      backend: backendFactory,
      checkpointer,
      ...createDeepAgentOptions,
    });

    // Wrap the agent so the sandbox is acquired (async) before each
    // invocation. The sync backend factory then finds it in the cache.
    // This avoids a dedicated setup node while keeping agent steps at the
    // top level for streaming visibility.
    const manager = this;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const handler: ProxyHandler<any> = {
      get(target, prop, receiver) {
        const value = Reflect.get(target, prop, receiver);
        if (
          typeof value === "function" &&
          (prop === "invoke" || prop === "ainvoke" || prop === "stream" || prop === "streamEvents")
        ) {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          return async function (this: unknown, input: unknown, config?: any, ...rest: unknown[]) {
            const threadId = config?.configurable?.thread_id as string | undefined;
            if (threadId) {
              await manager._ensureSandbox(threadId);
            }
            return value.call(target, input, config, ...rest);
          };
        }
        return value;
      },
    };
    return new Proxy(agent, handler);
  }

  // ── LangGraph node factory (backward-compatible) ───────────────────────────

  /**
   * Return an async LangGraph node function that manages the sandbox lifecycle.
   *
   * Kept for backward compatibility. For new applications prefer
   * {@link createAgent}, which uses a two-node streaming-compatible architecture.
   *
   * @param model - A LangChain `BaseChatModel` (or compatible).
   * @param stateSandboxKey - State field holding the sandbox ID. Defaults to `"sandboxId"`.
   * @param createDeepAgentOptions - Extra options forwarded to `createDeepAgent()`.
   */
  createAgentNode(
    model: AnyModel,
    {
      stateSandboxKey = "sandboxId",
      ...createDeepAgentOptions
    }: {
      stateSandboxKey?: string;
      [key: string]: unknown;
    } = {}
  ): (state: Record<string, unknown>, config?: unknown) => Promise<Record<string, unknown>> {
    const manager = this;

    return async function agentNode(
      state: Record<string, unknown>,
      config?: unknown
    ): Promise<Record<string, unknown>> {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const { createDeepAgent } = await import("deepagents" as any);

      const sandboxId = state[stateSandboxKey] as string | undefined;
      const sandbox = await manager._getOrReconnect(sandboxId);
      const agent = createDeepAgent(model, { backend: sandbox, ...createDeepAgentOptions });

      const messages = (state["messages"] as unknown[]) ?? [];
      const result = await agent.invoke({ messages }, config ?? {});

      const updates: Record<string, unknown> = {
        messages: (result as Record<string, unknown>)["messages"] ?? [],
      };

      if (sandbox.id !== sandboxId) {
        updates[stateSandboxKey] = sandbox.id;
      }

      return updates;
    };
  }

  // ── Lower-level helper: acquire sandbox from state ─────────────────────────

  /**
   * Reconnect to `sandboxId` if given and alive, else create a new sandbox.
   *
   * Delegates to {@link KubernetesProvider.getOrCreate}. Useful when you want
   * to manage the node logic yourself instead of using {@link createAgentNode}.
   *
   * @param sandboxId - An existing sandbox ID from graph state, or `undefined`.
   * @returns {@link KubernetesSandbox} whose `.id` may differ from `sandboxId`
   *   when a new sandbox was provisioned.
   */
  async getOrReconnect(sandboxId: string | undefined): Promise<KubernetesSandbox> {
    return this._getOrReconnect(sandboxId);
  }

  /** @internal */
  private async _getOrReconnect(sandboxId: string | undefined): Promise<KubernetesSandbox> {
    return this._provider.getOrCreate({
      sandboxId,
      labels: this.defaultLabels,
      ttlSeconds: this.ttlSeconds,
      ttlIdleSeconds: this.ttlIdleSeconds,
    });
  }

  // ── Operational: cleanup / shutdown ────────────────────────────────────────

  /**
   * Delete all managed sandboxes that have exceeded their TTL.
   *
   * @param maxIdleSeconds - Override idle threshold for this call.
   */
  async cleanup(maxIdleSeconds?: number): Promise<CleanupResult> {
    return this._provider.cleanup(maxIdleSeconds);
  }

  /**
   * Delete all sandboxes managed by this provider instance.
   */
  async shutdown(): Promise<void> {
    try {
      await this._provider.cleanup();
    } catch {
      // Errors are non-fatal during shutdown
    }
  }

  /**
   * Async dispose — called automatically when used with `await using`.
   */
  async [Symbol.asyncDispose](): Promise<void> {
    await this.shutdown();
  }
}
