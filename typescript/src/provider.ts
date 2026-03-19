import type { KubernetesProviderConfig } from "./config.js";
import { resolveConfig, validateConfig } from "./config.js";
import { KubernetesSandbox } from "./sandbox.js";
import { AgentSandboxBackend } from "./backends/agent-sandbox.js";
import { RawK8sBackend } from "./backends/raw.js";
import type { SandboxInfo as AgentSandboxInfo } from "./router-client.js";
import { SandboxRouterClient } from "./router-client.js";
import type { RawSandboxInfo } from "./backends/raw.js";
import { SandboxNotFoundError } from "./errors.js";
import {
  ANN_CREATED_AT,
  ANN_LAST_ACTIVITY,
  ANN_TTL_IDLE_SECONDS,
  ANN_TTL_SECONDS,
  LABEL_PREFIX,
  LK_LABEL_MANAGED_BY,
  LK_LABEL_POOL_STATUS,
  LK_LABEL_THREAD_ID,
  LK_MANAGED_BY_VALUE,
  LK_MANAGED_SELECTOR,
  POOL_STATUS_ACTIVE,
  POOL_STATUS_WARM,
  buildLabels,
  buildTtlAnnotations,
  nowIso,
  sanitizeLabelValue,
  threadIdSelector,
  warmPoolSelector,
} from "./labels.js";

/** A single entry in a sandbox list response. */
export interface SandboxInfo {
  id: string;
  namespace: string;
  threadId?: string;
  labels?: Record<string, string>;
  annotations?: Record<string, string>;
  createdAt?: string;
  lastActivity?: string;
  phase?: string;
  status?: string; // "running" | "warm" | "pending" | "terminated"
}

/** Paginated list response. */
export interface SandboxListResponse {
  sandboxes: SandboxInfo[];
  cursor?: string;
}

/** Result of a cleanup() operation. */
export interface CleanupResult {
  /** Sandbox IDs that were deleted. */
  deleted: string[];
  /** Number of sandboxes that were within their TTL / idle threshold. */
  kept: number;
}

/** Status of the warm pool. */
export interface WarmPoolStatus {
  available: number;
  active: number;
  total: number;
  target: number;
}

/** Aggregate statistics for the provider. */
export interface ProviderStats {
  total: number;
  running: number;
  warm: number;
  idle: number;
  threadIds: number;
}

/** Options for getOrCreate(). */
export interface GetOrCreateOptions {
  /** Thread/conversation identifier for label-based lookup. */
  threadId?: string;
  /** Per-call labels (keys are auto-prefixed). */
  labels?: Record<string, string>;
  /** Absolute TTL from creation (seconds). Overrides config.ttlSeconds. */
  ttlSeconds?: number;
  /** Idle TTL from last execute() (seconds). Overrides config.ttlIdleSeconds. */
  ttlIdleSeconds?: number;
}

/**
 * Manages the lifecycle of Kubernetes sandbox environments.
 *
 * Supports two backend modes, selected via `config.mode`:
 *
 * **`agent-sandbox` mode** (default, recommended):
 * - Requires: `routerUrl` and `templateName` in config.
 * - Requires: `kubernetes-sigs/agent-sandbox` controller + CRDs installed.
 * - Benefits: warm pools, gVisor/Kata isolation, sub-second startup.
 *
 * **`raw` mode** (fallback):
 * - Requires: `@kubernetes/client-node` and `tar-stream` installed.
 * - Works on any cluster — no CRDs needed.
 *
 * @example
 * ```typescript
 * const provider = new KubernetesProvider({
 *   mode: "agent-sandbox",
 *   routerUrl: "http://sandbox-router-svc.default.svc.cluster.local:8080",
 *   templateName: "python-sandbox-template",
 * });
 *
 * // Per-thread sandbox (idempotent)
 * const sandbox = await provider.getOrCreate({
 *   threadId: "conv-abc-123",
 *   ttlSeconds: 3600,
 * });
 * ```
 */
export class KubernetesProvider {
  private readonly config: KubernetesProviderConfig;
  // In-process cache: sandboxId → KubernetesSandbox
  private readonly sandboxCache = new Map<string, KubernetesSandbox>();
  // thread_id → sandboxId
  private readonly threadIdMap = new Map<string, string>();
  private warmPoolInitialised = false;

  constructor(config?: Partial<KubernetesProviderConfig>) {
    this.config = resolveConfig(config);
    validateConfig(this.config);
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  /**
   * Get an existing sandbox or create a new one.
   *
   * When `threadId` is provided, looks for an existing sandbox for that thread
   * via a Kubernetes label selector before creating a new one.
   *
   * @param sandboxIdOrOptions - Existing sandbox ID to reconnect to, or
   *   an options object `{ threadId?, labels?, ttlSeconds?, ttlIdleSeconds? }`.
   *   Pass `undefined` to always create new.
   */
  async getOrCreate(
    sandboxIdOrOptions?: string | GetOrCreateOptions
  ): Promise<KubernetesSandbox> {
    const opts: GetOrCreateOptions =
      typeof sandboxIdOrOptions === "string"
        ? {}
        : (sandboxIdOrOptions ?? {});
    const sandboxId = typeof sandboxIdOrOptions === "string" ? sandboxIdOrOptions : undefined;

    const mode = this.config.mode ?? "agent-sandbox";

    // Lazy warm-pool init (raw mode only)
    if (mode === "raw" && (this.config.warmPoolSize ?? 0) > 0 && !this.warmPoolInitialised) {
      this.warmPoolInitialised = true;
      this.replenishWarmPool().catch(() => undefined);
    }

    // Effective TTL values
    const effTtl = opts.ttlSeconds ?? this.config.ttlSeconds;
    const effIdle = opts.ttlIdleSeconds ?? this.config.ttlIdleSeconds;

    // Build merged labels and annotations
    const [extraLabels, extraAnnotations] = buildLabels({
      defaultLabels: this.config.defaultLabels,
      callLabels: opts.labels,
      threadId: opts.threadId,
    });
    Object.assign(extraAnnotations, buildTtlAnnotations({
      ttlSeconds: effTtl,
      ttlIdleSeconds: effIdle,
    }));

    // Thread_id lookup
    if (opts.threadId) {
      const existing = await this.findByThreadIdInternal(opts.threadId, effIdle);
      if (existing) return existing;
    }

    // sandbox_id reconnect
    if (sandboxId) {
      const cached = this.sandboxCache.get(sandboxId);
      if (cached) return cached;
      // Not in cache — delegate to mode-specific reconnect
      return this.reconnect(sandboxId);
    }

    // Warm-pool claim (raw mode with threadId)
    if (mode === "raw" && (this.config.warmPoolSize ?? 0) > 0 && opts.threadId) {
      const warmSandbox = await this.claimWarmPod(opts.threadId, extraLabels, extraAnnotations, effIdle);
      if (warmSandbox) {
        if (opts.threadId) this.threadIdMap.set(opts.threadId, warmSandbox.id);
        return warmSandbox;
      }
    }

    // Create new sandbox
    const sandbox = await this.create(extraLabels, extraAnnotations, effIdle);
    if (opts.threadId) this.threadIdMap.set(opts.threadId, sandbox.id);
    return sandbox;
  }

  /**
   * Look up a sandbox by thread identifier without creating one.
   *
   * @param threadId - Thread/conversation identifier.
   * @returns The sandbox if found, otherwise `undefined`.
   */
  async findByThreadId(threadId: string): Promise<KubernetesSandbox | undefined> {
    return this.findByThreadIdInternal(threadId, undefined);
  }

  /**
   * List sandboxes from the Kubernetes API with optional filtering.
   *
   * @param options - Filter and pagination options.
   */
  async list(options?: {
    cursor?: string;
    labels?: Record<string, string>;
    threadId?: string;
    status?: string;
  }): Promise<SandboxListResponse> {
    const mode = this.config.mode ?? "agent-sandbox";
    if (mode === "agent-sandbox") {
      return this.listAgentSandbox(options);
    }
    return this.listRaw(options);
  }

  /**
   * Delete a sandbox. Idempotent — deleting a non-existent sandbox is a no-op.
   * After deletion, schedules warm-pool replenishment if enabled.
   *
   * @param sandboxId - The sandbox ID to delete.
   */
  async delete(sandboxId: string): Promise<void> {
    const mode = this.config.mode ?? "agent-sandbox";
    this.sandboxCache.delete(sandboxId);
    for (const [tid, sid] of this.threadIdMap.entries()) {
      if (sid === sandboxId) this.threadIdMap.delete(tid);
    }

    if (mode === "agent-sandbox") {
      await AgentSandboxBackend.deleteSandbox(sandboxId, this.config);
    } else {
      await RawK8sBackend.deleteSandbox(sandboxId, this.config);
    }

    // Replenish warm pool
    if (mode === "raw" && (this.config.warmPoolSize ?? 0) > 0) {
      this.replenishWarmPool().catch(() => undefined);
    }
  }

  /**
   * Delete sandboxes that have exceeded their TTL or idle threshold.
   *
   * @param maxIdleSeconds - Override idle threshold for this call.
   * @returns {@link CleanupResult} with deleted IDs and a count of kept sandboxes.
   */
  async cleanup(maxIdleSeconds?: number): Promise<CleanupResult> {
    const result: CleanupResult = { deleted: [], kept: 0 };
    const now = Date.now();
    const response = await this.list();

    for (const info of response.sandboxes) {
      const ann = info.annotations ?? {};
      let shouldDelete = false;

      // Check absolute TTL
      const ttlStr = ann[ANN_TTL_SECONDS];
      const createdStr = ann[ANN_CREATED_AT];
      if (ttlStr && createdStr) {
        const ttl = parseInt(ttlStr, 10);
        const created = new Date(createdStr).getTime();
        if (!isNaN(ttl) && !isNaN(created) && (now - created) / 1000 > ttl) {
          shouldDelete = true;
        }
      }

      // Check idle TTL
      let idleThreshold = maxIdleSeconds;
      if (idleThreshold === undefined) {
        const idleStr = ann[ANN_TTL_IDLE_SECONDS];
        if (idleStr) {
          const parsed = parseInt(idleStr, 10);
          if (!isNaN(parsed)) idleThreshold = parsed;
        }
      }
      if (idleThreshold !== undefined) {
        const lastStr = ann[ANN_LAST_ACTIVITY] ?? ann[ANN_CREATED_AT];
        if (lastStr) {
          const last = new Date(lastStr).getTime();
          if (!isNaN(last) && (now - last) / 1000 > idleThreshold) {
            shouldDelete = true;
          }
        }
      }

      if (shouldDelete) {
        try {
          await this.delete(info.id);
          result.deleted.push(info.id);
        } catch {
          // Log and continue
        }
      } else {
        result.kept++;
      }
    }

    return result;
  }

  /**
   * Return aggregate statistics for all managed sandboxes.
   *
   * @param idleThresholdSeconds - Seconds since last execute() before considered idle. Default 300.
   */
  async stats(idleThresholdSeconds = 300): Promise<ProviderStats> {
    const response = await this.list();
    const now = Date.now();
    let running = 0, warm = 0, idle = 0;
    const threadIds = new Set<string>();

    for (const info of response.sandboxes) {
      if (info.status === "running") running++;
      else if (info.status === "warm") warm++;
      if (info.threadId) threadIds.add(info.threadId);

      const lastStr = info.annotations?.[ANN_LAST_ACTIVITY] ?? info.annotations?.[ANN_CREATED_AT];
      if (lastStr && info.status === "running") {
        const last = new Date(lastStr).getTime();
        if (!isNaN(last) && (now - last) / 1000 > idleThresholdSeconds) idle++;
      }
    }

    return {
      total: response.sandboxes.length,
      running,
      warm,
      idle,
      threadIds: threadIds.size,
    };
  }

  /**
   * Return the current warm-pool status.
   */
  async poolStatus(): Promise<WarmPoolStatus> {
    const mode = this.config.mode ?? "agent-sandbox";
    if (mode !== "raw") {
      const response = await this.list();
      const active = response.sandboxes.filter((s) => s.status === "running").length;
      return { available: 0, active, total: active, target: 0 };
    }

    try {
      const { coreApi } = await this.loadRawClients();
      const warmList = await coreApi.listNamespacedPod({
        namespace: this.config.namespace,
        labelSelector: warmPoolSelector(),
      });
      const available = (warmList.items ?? []).filter(
        (p: { status?: { phase?: string } }) => p.status?.phase === "Running" || p.status?.phase === "Pending"
      ).length;

      const activeList = await coreApi.listNamespacedPod({
        namespace: this.config.namespace,
        labelSelector: `${LK_LABEL_POOL_STATUS}=${POOL_STATUS_ACTIVE}`,
      });
      const active = (activeList.items ?? []).length;
      return {
        available,
        active,
        total: available + active,
        target: this.config.warmPoolSize ?? 0,
      };
    } catch {
      return { available: 0, active: 0, total: 0, target: this.config.warmPoolSize ?? 0 };
    }
  }

  // ── Private: create / reconnect ────────────────────────────────────────────

  /** Create a fresh sandbox. */
  private async create(
    extraLabels?: Record<string, string>,
    extraAnnotations?: Record<string, string>,
    ttlIdleSeconds?: number
  ): Promise<KubernetesSandbox> {
    const mode = this.config.mode ?? "agent-sandbox";

    if (mode === "agent-sandbox") {
      const backend = await AgentSandboxBackend.create(this.config, extraLabels, extraAnnotations);
      const sandbox = new KubernetesSandbox(backend);
      // Patch claim with our labels after creation
      if (extraLabels && Object.keys(extraLabels).length > 0) {
        const client = buildRouterClient(this.config);
        client.patchSandboxClaim(backend.id, extraLabels, extraAnnotations ?? {}).catch(() => undefined);
      }
      // Set up idle activity tracking
      if (ttlIdleSeconds !== undefined) {
        const claimName = backend.id;
        const client = buildRouterClient(this.config);
        sandbox.setActivityCallback(() => {
          client.patchSandboxClaim(claimName, {}, { [ANN_LAST_ACTIVITY]: nowIso() }).catch(() => undefined);
        });
      }
      this.sandboxCache.set(backend.id, sandbox);
      return sandbox;
    } else {
      const backend = await RawK8sBackend.create(this.config, undefined, extraLabels, extraAnnotations, ttlIdleSeconds);
      const sandbox = new KubernetesSandbox(backend);
      this.sandboxCache.set(backend.id, sandbox);
      return sandbox;
    }
  }

  /** Reconnect to an existing sandbox by ID. */
  private async reconnect(sandboxId: string): Promise<KubernetesSandbox> {
    const mode = this.config.mode ?? "agent-sandbox";

    if (mode === "agent-sandbox") {
      const backend = await AgentSandboxBackend.reconnect(sandboxId, this.config);
      const sandbox = new KubernetesSandbox(backend);
      this.sandboxCache.set(sandboxId, sandbox);
      return sandbox;
    } else {
      try {
        const backend = await RawK8sBackend.reconnect(sandboxId, this.config);
        const sandbox = new KubernetesSandbox(backend);
        this.sandboxCache.set(sandboxId, sandbox);
        return sandbox;
      } catch (err: unknown) {
        if (err instanceof SandboxNotFoundError) throw err;
        throw err;
      }
    }
  }

  // ── Private: thread_id lookup ───────────────────────────────────────────────

  private async findByThreadIdInternal(
    threadId: string,
    ttlIdleSeconds: number | undefined
  ): Promise<KubernetesSandbox | undefined> {
    // Check in-process cache
    const cachedId = this.threadIdMap.get(threadId);
    if (cachedId) {
      const cached = this.sandboxCache.get(cachedId);
      if (cached) return cached;
    }

    const mode = this.config.mode ?? "agent-sandbox";
    if (mode === "raw") {
      return this.findByThreadIdRaw(threadId, ttlIdleSeconds);
    }
    return this.findByThreadIdAgentSandbox(threadId, ttlIdleSeconds);
  }

  private async findByThreadIdRaw(
    threadId: string,
    ttlIdleSeconds: number | undefined
  ): Promise<KubernetesSandbox | undefined> {
    try {
      const { coreApi, networkingApi } = await this.loadRawClients();
      const selector = threadIdSelector(threadId);
      const podList = await coreApi.listNamespacedPod({
        namespace: this.config.namespace,
        labelSelector: selector,
      });

      for (const pod of podList.items ?? []) {
        if (pod.status?.phase !== "Running") continue;
        const backend = await RawK8sBackend.reconnect(
          this.extractRawSandboxId(pod),
          this.config
        );
        if (ttlIdleSeconds !== undefined) {
          (backend as { ttlIdleSeconds?: number }).ttlIdleSeconds = ttlIdleSeconds;
        }
        const sandbox = new KubernetesSandbox(backend);
        this.sandboxCache.set(backend.id, sandbox);
        this.threadIdMap.set(threadId, backend.id);
        return sandbox;
      }
    } catch {
      // Log warning and fall through
    }
    return undefined;
  }

  private async findByThreadIdAgentSandbox(
    threadId: string,
    ttlIdleSeconds: number | undefined
  ): Promise<KubernetesSandbox | undefined> {
    try {
      const client = buildRouterClient(this.config);
      const items = await client.listSandboxClaims(threadIdSelector(threadId));
      for (const item of items) {
        const obj = item as Record<string, unknown>;
        const meta = (obj["metadata"] ?? {}) as Record<string, unknown>;
        const name = (meta["name"] as string | undefined) ?? "";
        if (!name) continue;

        const backend = await AgentSandboxBackend.reconnect(name, this.config);
        const sandbox = new KubernetesSandbox(backend);
        if (ttlIdleSeconds !== undefined) {
          sandbox.setActivityCallback(() => {
            client.patchSandboxClaim(name, {}, { [ANN_LAST_ACTIVITY]: nowIso() }).catch(() => undefined);
          });
        }
        this.sandboxCache.set(name, sandbox);
        this.threadIdMap.set(threadId, name);
        return sandbox;
      }
    } catch {
      // Fall through
    }
    return undefined;
  }

  // ── Private: list ──────────────────────────────────────────────────────────

  private async listRaw(options?: {
    cursor?: string;
    labels?: Record<string, string>;
    threadId?: string;
    status?: string;
  }): Promise<SandboxListResponse> {
    try {
      const { coreApi } = await this.loadRawClients();

      let selector = LK_MANAGED_SELECTOR;
      if (options?.labels) {
        for (const [k, v] of Object.entries(options.labels)) {
          selector += `,${LABEL_PREFIX}${k}=${v}`;
        }
      }
      if (options?.threadId) {
        const [safe] = sanitizeLabelValue(options.threadId);
        selector += `,${LK_LABEL_THREAD_ID}=${safe}`;
      }

      const listResult = await coreApi.listNamespacedPod({
        namespace: this.config.namespace,
        labelSelector: selector,
        _continue: options?.cursor,
      } as Record<string, unknown>);

      const sandboxes: SandboxInfo[] = (listResult.items ?? [])
        .map((pod: Record<string, unknown>) => podToSandboxInfo(pod))
        .filter((s: SandboxInfo) => !options?.status || s.status === options.status);

      const nextCursor = (listResult.metadata as Record<string, unknown> | undefined)?.[
        "continue"
      ] as string | undefined;

      return { sandboxes, cursor: nextCursor };
    } catch {
      // Fall back to in-process cache
      const sandboxes: SandboxInfo[] = Array.from(this.sandboxCache.entries()).map(
        ([id]) => ({ id, namespace: this.config.namespace, status: "running" })
      );
      return { sandboxes };
    }
  }

  private async listAgentSandbox(options?: {
    cursor?: string;
    labels?: Record<string, string>;
    threadId?: string;
    status?: string;
  }): Promise<SandboxListResponse> {
    try {
      const client = buildRouterClient(this.config);

      let selector = LK_MANAGED_SELECTOR;
      if (options?.labels) {
        for (const [k, v] of Object.entries(options.labels)) {
          selector += `,${LABEL_PREFIX}${k}=${v}`;
        }
      }
      if (options?.threadId) {
        const [safe] = sanitizeLabelValue(options.threadId);
        selector += `,${LK_LABEL_THREAD_ID}=${safe}`;
      }

      const items = await client.listSandboxClaims(selector);
      const sandboxes: SandboxInfo[] = items
        .map((item) => claimToSandboxInfo(item, this.config.namespace))
        .filter((s) => !options?.status || s.status === options.status);

      return { sandboxes };
    } catch {
      // Fall back to in-process cache
      const sandboxes: SandboxInfo[] = Array.from(this.sandboxCache.entries()).map(
        ([id]) => ({ id, namespace: this.config.namespace, status: "running" })
      );
      return { sandboxes };
    }
  }

  // ── Private: warm pool ─────────────────────────────────────────────────────

  private async claimWarmPod(
    threadId: string,
    extraLabels: Record<string, string>,
    extraAnnotations: Record<string, string>,
    ttlIdleSeconds: number | undefined
  ): Promise<KubernetesSandbox | undefined> {
    try {
      const { coreApi } = await this.loadRawClients();
      const warmList = await coreApi.listNamespacedPod({
        namespace: this.config.namespace,
        labelSelector: warmPoolSelector(),
      });

      for (const pod of warmList.items ?? []) {
        if (pod.status?.phase !== "Running" && pod.status?.phase !== "Pending") continue;

        const podName = pod.metadata?.name;
        const namespace = pod.metadata?.namespace ?? this.config.namespace;
        if (!podName) continue;

        const [safeTid] = sanitizeLabelValue(threadId);
        const patchLabels = {
          ...extraLabels,
          [LK_LABEL_THREAD_ID]: safeTid,
          [LK_LABEL_POOL_STATUS]: POOL_STATUS_ACTIVE,
        };

        await coreApi.patchNamespacedPod({
          name: podName,
          namespace,
          body: { metadata: { labels: patchLabels, annotations: extraAnnotations } },
        });

        const sandboxId = this.extractRawSandboxId(pod);
        const backend = await RawK8sBackend.reconnect(sandboxId, this.config);
        if (ttlIdleSeconds !== undefined) {
          (backend as { ttlIdleSeconds?: number }).ttlIdleSeconds = ttlIdleSeconds;
        }
        const sandbox = new KubernetesSandbox(backend);
        this.sandboxCache.set(sandboxId, sandbox);
        return sandbox;
      }
    } catch {
      // Fall through to cold create
    }
    return undefined;
  }

  private async replenishWarmPool(): Promise<void> {
    const target = this.config.warmPoolSize ?? 0;
    if (target <= 0) return;

    try {
      const { coreApi } = await this.loadRawClients();
      const warmList = await coreApi.listNamespacedPod({
        namespace: this.config.namespace,
        labelSelector: warmPoolSelector(),
      });
      const current = (warmList.items ?? []).filter(
        (p: { status?: { phase?: string } }) =>
          p.status?.phase !== "Failed" &&
          p.status?.phase !== "Unknown" &&
          p.status?.phase !== "Succeeded"
      ).length;

      const needed = target - current;
      if (needed <= 0) return;

      const poolLabels = {
        [LK_LABEL_MANAGED_BY]: LK_MANAGED_BY_VALUE,
        [LK_LABEL_POOL_STATUS]: POOL_STATUS_WARM,
      };

      for (let i = 0; i < needed; i++) {
        try {
          await RawK8sBackend.create(this.config, undefined, poolLabels);
        } catch {
          // Non-fatal
        }
      }
    } catch {
      // Non-fatal
    }
  }

  // ── Private: helpers ────────────────────────────────────────────────────────

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private async loadRawClients(): Promise<{ coreApi: any; networkingApi: any }> {
    const { loadK8sClients } = await import("./backends/raw.js");
    return loadK8sClients(this.config);
  }

  private extractRawSandboxId(pod: { metadata?: { name?: string; labels?: Record<string, string> } }): string {
    const name = pod.metadata?.name ?? "";
    const labels = pod.metadata?.labels ?? {};
    const LABEL_SANDBOX_ID = "deepagents.langchain.com/sandbox-id";
    return labels[LABEL_SANDBOX_ID] ?? name.replace(/^deepagents-/, "");
  }
}

// ── Private helpers ────────────────────────────────────────────────────────────

function buildRouterClient(config: KubernetesProviderConfig): SandboxRouterClient {
  return new SandboxRouterClient(config.routerUrl!, {
    namespace: config.namespace,
    serverPort: config.serverPort ?? 8888,
    kubeApiUrl: config.kubeApiUrl,
    kubeToken: config.kubeToken,
    sandboxReadyTimeoutMs: config.startupTimeoutMs ?? 180_000,
  });
}

function podToSandboxInfo(pod: unknown): SandboxInfo {
  const p = pod as Record<string, unknown>;
  const meta = (p["metadata"] ?? {}) as Record<string, unknown>;
  const status = (p["status"] ?? {}) as Record<string, unknown>;

  const name = (meta["name"] as string) ?? "unknown";
  const namespace = (meta["namespace"] as string) ?? "default";
  const labels = ((meta["labels"] as Record<string, string>) ?? {});
  const annotations = ((meta["annotations"] as Record<string, string>) ?? {});
  const phase = (status["phase"] as string | undefined);

  const poolStatus = labels[LK_LABEL_POOL_STATUS];
  const threadId = labels[LK_LABEL_THREAD_ID];
  const LABEL_SANDBOX_ID = "deepagents.langchain.com/sandbox-id";
  const sandboxId = labels[LABEL_SANDBOX_ID] ?? name.replace(/^deepagents-/, "");

  let sandboxStatus: string;
  if (poolStatus === POOL_STATUS_WARM) {
    sandboxStatus = "warm";
  } else if (phase === "Running") {
    sandboxStatus = "running";
  } else if (phase === "Succeeded" || phase === "Failed") {
    sandboxStatus = "terminated";
  } else {
    sandboxStatus = (phase?.toLowerCase()) ?? "pending";
  }

  return {
    id: sandboxId,
    namespace,
    threadId,
    labels,
    annotations,
    createdAt: annotations[ANN_CREATED_AT],
    lastActivity: annotations[ANN_LAST_ACTIVITY],
    phase,
    status: sandboxStatus,
  };
}

function claimToSandboxInfo(item: unknown, defaultNamespace: string): SandboxInfo {
  const obj = (item as Record<string, unknown>) ?? {};
  const meta = ((obj["metadata"] as Record<string, unknown>) ?? {});
  const name = (meta["name"] as string) ?? "unknown";
  const namespace = (meta["namespace"] as string) ?? defaultNamespace;
  const labels = ((meta["labels"] as Record<string, string>) ?? {});
  const annotations = ((meta["annotations"] as Record<string, string>) ?? {});

  const threadId = labels[LK_LABEL_THREAD_ID];

  const statusObj = (obj["status"] as Record<string, unknown> | undefined) ?? {};
  const conditions = (statusObj["conditions"] as unknown[]) ?? [];
  const ready = conditions.some((c) => {
    const cond = c as Record<string, unknown>;
    return cond["type"] === "Ready" && cond["status"] === "True";
  });

  return {
    id: name,
    namespace,
    threadId,
    labels,
    annotations,
    createdAt: annotations[ANN_CREATED_AT],
    lastActivity: annotations[ANN_LAST_ACTIVITY],
    status: ready ? "running" : "pending",
  };
}
