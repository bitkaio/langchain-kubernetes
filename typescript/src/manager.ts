/**
 * KubernetesSandboxManager — LangGraph-integrated sandbox lifecycle manager.
 *
 * Provides {@link KubernetesSandboxManager.createAgentNode} — a factory that
 * returns a LangGraph node function managing the complete sandbox lifecycle.
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
 * Wraps a stateless {@link KubernetesProvider} and provides
 * {@link createAgentNode} — a factory that returns a LangGraph node function
 * handling the complete sandbox lifecycle:
 *
 * 1. Read `sandboxId` from graph state (`null`/`undefined` on first run).
 * 2. Reconnect to the existing sandbox if `sandboxId` is set and still alive.
 * 3. Provision a new sandbox if none exists or the previous one expired.
 * 4. Run the DeepAgents agent with the sandbox for this invocation.
 * 5. Write the (possibly new) `sandboxId` back to graph state so LangGraph's
 *    checkpointer persists it for the next run.
 *
 * **No state is held in memory.** The `sandboxId` lives exclusively in the
 * LangGraph graph state and is persisted by whichever checkpointer the user
 * configures (`MemorySaver`, `PostgresSaver`, `RedisSaver`, …). This means
 * the integration works transparently across process restarts, horizontal
 * scaling, and the LangGraph Platform without any Kubernetes label machinery.
 *
 * @example Minimal LangGraph integration
 * ```typescript
 * import { StateGraph, END, MemorySaver } from "@langchain/langgraph";
 * import { Annotation } from "@langchain/langgraph";
 * import { KubernetesSandboxManager } from "@bitkaio/langchain-kubernetes";
 *
 * const AgentState = Annotation.Root({
 *   messages: Annotation<BaseMessage[]>({ reducer: (a, b) => a.concat(b) }),
 *   sandboxId: Annotation<string | undefined>({ reducer: (_, b) => b }),
 * });
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
 * const builder = new StateGraph(AgentState)
 *   .addNode("agent", manager.createAgentNode(model))
 *   .addEdge("__start__", "agent")
 *   .addEdge("agent", END);
 *
 * const graph = builder.compile({ checkpointer: new MemorySaver() });
 * ```
 */
export class KubernetesSandboxManager {
  /** @internal */
  readonly _provider: KubernetesProvider;

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
   * Return a compiled DeepAgents graph with automatic per-conversation sandbox management.
   *
   * This is the recommended integration point for most applications. Equivalent to
   * building a `StateGraph` with {@link createAgentNode} but without the boilerplate.
   * Invoke with `config: { configurable: { thread_id: "..." } }` to route each
   * conversation to its own persistent sandbox.
   *
   * @param model - A LangChain `BaseChatModel` (or compatible) passed to
   *   `createDeepAgent()`.
   * @param options.checkpointer - LangGraph checkpointer (`MemorySaver`,
   *   `PostgresSaver`, …). Required when calling `.invoke()` directly (e.g. in
   *   Express). The LangGraph Platform / `langgraph dev` provide their own
   *   checkpointer — omit it there.
   * @param options.stateSandboxKey - State field name for the sandbox ID.
   *   Defaults to `"sandboxId"`.
   * @param options - Extra options forwarded to `createDeepAgent()`.
   *
   * @example FastAPI / Express — pass a checkpointer
   * ```typescript
   * const agent = await manager.createAgent(llm, { checkpointer: new MemorySaver() });
   * const result = await agent.invoke(
   *   { messages: [{ role: "user", content: "hello" }] },
   *   { configurable: { thread_id: "conv-1" } },
   * );
   * ```
   *
   * @example langgraph dev / LangGraph Platform — no checkpointer needed
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
    const { StateGraph, END, Annotation } = await import("@langchain/langgraph" as any);

    const AgentState = Annotation.Root({
      messages: Annotation({
        reducer: (a: unknown[], b: unknown[]) => [...(a ?? []), ...(Array.isArray(b) ? b : [b])],
      }),
      [stateSandboxKey]: Annotation({
        reducer: (_: unknown, b: unknown) => b,
      }),
    });

    const graph = new StateGraph(AgentState)
      .addNode("agent", this.createAgentNode(model, { stateSandboxKey, ...createDeepAgentOptions }))
      .addEdge("__start__", "agent")
      .addEdge("agent", END)
      .compile(checkpointer ? { checkpointer } : undefined);

    return graph;
  }

  // ── LangGraph node factory ─────────────────────────────────────────────────

  /**
   * Return an async LangGraph node function that manages the sandbox lifecycle.
   *
   * The returned node reads `state[stateSandboxKey]` to reconnect an existing
   * sandbox or create a new one, runs the DeepAgents agent against the current
   * messages, and returns updated messages plus the (possibly new) `sandboxId`
   * to be stored back in graph state.
   *
   * **Graph state requirements** — your state annotation must include:
   * ```typescript
   * messages: Annotation<BaseMessage[]>({ reducer: (a, b) => a.concat(b) })
   * sandboxId: Annotation<string | undefined>({ reducer: (_, b) => b })
   * ```
   *
   * @param model - A LangChain `BaseChatModel` (or compatible) passed to
   *   `createDeepAgent()`.
   * @param stateSandboxKey - Name of the state field holding the sandbox ID.
   *   Defaults to `"sandboxId"`.
   * @param createDeepAgentOptions - Extra options forwarded to `createDeepAgent()`
   *   (e.g. `tools`, `systemPrompt`).
   *
   * @example
   * ```typescript
   * builder.addNode("agent", manager.createAgentNode(model, {
   *   systemPrompt: "You are a helpful data analyst.",
   * }));
   * ```
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

      // Acquire sandbox — reconnects if sandboxId is valid, creates otherwise
      const sandbox = await manager._getOrReconnect(sandboxId);

      // Build a fresh agent bound to this sandbox for the current invocation
      const agent = createDeepAgent(model, { backend: sandbox, ...createDeepAgentOptions });

      const messages = (state["messages"] as unknown[]) ?? [];
      const result = await agent.invoke({ messages }, config ?? {});

      const updates: Record<string, unknown> = {
        messages: (result as Record<string, unknown>)["messages"] ?? [],
      };

      // Persist sandboxId if it changed (new sandbox was provisioned)
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
   * Delegates to {@link KubernetesProvider.cleanup}, which queries the
   * Kubernetes API directly and removes expired resources.
   *
   * @param maxIdleSeconds - Override idle threshold for this call.
   */
  async cleanup(maxIdleSeconds?: number): Promise<CleanupResult> {
    return this._provider.cleanup(maxIdleSeconds);
  }

  /**
   * Delete all sandboxes managed by this provider instance.
   *
   * Runs {@link cleanup} without an idle threshold, removing all managed
   * sandboxes regardless of their remaining TTL. Useful for tearing down
   * a dev environment or a batch job on exit.
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
