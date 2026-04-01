# Changelog — @bitkaio/langchain-kubernetes (TypeScript)

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [0.4.0] — 2026-04-01

### Changed

#### KubernetesSandboxManager — lazy sandbox acquisition, top-level deepagent

- `createAgent()` now returns a Proxy-wrapped deepagent graph (via `createDeepAgent()`) instead of wrapping it in a `StateGraph(setup → agent)`. All deepagent steps (todos, tool calls, LLM tokens) are emitted as top-level graph events — visible in the Deep Agent UI and LangGraph Platform streaming.
- The Proxy intercepts `invoke`, `ainvoke`, `stream`, and `streamEvents` to call `_ensureSandbox(threadId)` before delegating, so the sandbox is acquired lazily on first invocation — no dedicated setup node required.
- New `_ensureSandbox(threadId)` method (`@internal`): acquires a sandbox via `getOrCreate()` and caches it in `_sandboxByThread` if not already present; no-op when cached.
- `_makeBackendFactory()` is simplified: throws when sandbox is not cached (the Proxy wrapper guarantees preloading).
- `_sandboxByThread` instance cache is still populated, but now by `_ensureSandbox()` (called from the Proxy) rather than by a setup node.
- `createSetupNode()` and `createAgentNode()` are unchanged and kept for backward compatibility / custom graph builds.

---

## [0.3.0] — 2026-03-21

### Added

#### KubernetesSandboxManager

- New `KubernetesSandboxManager` class: stateless high-level wrapper for building DeepAgents-powered applications. Holds no sandbox cache and no pending Promise map — all sandbox-to-conversation binding lives in LangGraph graph state.
- `createAgent(model, { checkpointer? })`: async; returns a compiled LangGraph+DeepAgents graph. Stores `sandboxId` in graph state; the checkpointer (or LangGraph Platform) persists it between runs so each thread reconnects to the same sandbox automatically.
- `createAgentNode(model)`: returns a single async node function for embedding in a larger `StateGraph`.
- `getOrReconnect(sandboxId)`: lower-level helper; delegates to `provider.getOrCreate({ sandboxId, ... })`.
- `shutdown()`: deletes all sandboxes created by this provider instance; errors are logged but do not throw.
- `Symbol.asyncDispose` support — works with `await using`.
- `src/manager.ts`: new module containing `KubernetesSandboxManager` and `KubernetesSandboxManagerOptions`.

#### TTL and auto-cleanup

- New `ttlSeconds` and `ttlIdleSeconds` `KubernetesProviderConfig` fields: set `langchain-kubernetes.bitkaio.com/ttl-seconds`, `created-at`, and `ttl-idle-seconds` annotations at creation time.
- `execute()` updates `langchain-kubernetes.bitkaio.com/last-activity` annotation after each call (fire-and-forget, never blocks or throws).
- New `cleanup(maxIdleSeconds?)` method on `KubernetesProvider`: deletes sandboxes that have exceeded their absolute or idle TTL.
- New `stats(idleThresholdSeconds?)` method: returns aggregate `ProviderStats` (total, running, warm, idle, threadIds).
- New `list()` method: queries the Kubernetes API; supports filtering by `labels` and `status`; returns `SandboxListResponse`.
- New `poolStatus()` method: returns `WarmPoolStatus` (available, active, total, target).

#### Warm pool integration

- **agent-sandbox mode**: new `warmPoolName` config field — claims sandboxes from a named `SandboxWarmPool` CRD instead of cold-creating them.
- **raw mode**: new `warmPoolSize` config field — provider pre-creates idle Pods labelled `pool-status=warm`; `getOrCreate()` claims a warm Pod (patches label to `pool-status=active`) before falling back to cold creation; `delete()` triggers background replenishment.

#### Optional K8s API access in agent-sandbox mode

- `isK8sApiConfigured()` exported from `src/router-client.ts`: returns `true` when direct K8s API access is available (explicit `kubeApiUrl` / `kubeToken` config, or `KUBERNETES_SERVICE_HOST` env var). When configured, `reconnect()` verifies sandbox existence before returning; otherwise returns optimistically.
- `listSandboxClaims(labelSelector?)` method added to `SandboxRouterClient`.

#### New config fields (`KubernetesProviderConfig`)

- `defaultLabels` — applied to every sandbox.
- `ttlSeconds` — default absolute TTL.
- `ttlIdleSeconds` — default idle TTL.
- `warmPoolSize` — raw mode warm pool target size.
- `warmPoolName` — agent-sandbox mode warm pool CRD name.
- `kubeApiUrl` — Kubernetes API URL for optional direct K8s API access in agent-sandbox mode.
- `kubeToken` — Bearer token for Kubernetes API (auto-detects in-cluster via `KUBERNETES_SERVICE_HOST` if unset).

#### New exported types

- `SandboxInfo` — metadata entry returned by `list()`, including optional `threadId`.
- `SandboxListResponse` — list result with `.sandboxes` and optional `.cursor`.
- `CleanupResult` — result of `cleanup()` (`.deleted` IDs + `.kept` count).
- `WarmPoolStatus` — pool state snapshot (`.available`, `.active`, `.total`, `.target`).
- `ProviderStats` — aggregate statistics.
- `KubernetesSandboxManagerOptions` — constructor options bag for `KubernetesSandboxManager`.

#### New internal modules

- `src/labels.ts`: label/annotation constants, `sanitizeLabelValue()`, `buildLabels()`, `buildTtlAnnotations()`, `warmPoolSelector()`.

### Changed

- `getOrCreate()` now accepts an options bag including `sandboxId` for reconnection, `labels`, `ttlSeconds`, `ttlIdleSeconds` — fully backward-compatible.
- `list()` return type is now `SandboxListResponse` with a `.sandboxes` field. **Breaking change** for direct callers of `provider.list()`.
- `RawK8sBackend.create()` signature extended with optional `sandboxId`, `extraLabels`, `extraAnnotations`, `ttlIdleSeconds` parameters — fully backward-compatible.
- `buildPodManifest()` extended with optional `extraLabels`, `extraAnnotations` parameters — fully backward-compatible.

### Fixed

- `router-client.ts`: renamed inner `contentType` variable in response parsing to `respContentType` to resolve duplicate identifier compiler error.
- `raw-manifests.ts`: corrected always-truthy expression in annotation conditional (`{} || undefined` → proper ternary).

### CI

- Added `typescript:dev-publish` manual GitLab CI job in a new `dev-publish` stage; triggered via the GitLab UI or API from any branch, never run automatically.
- Version scheme: `{base}-dev.{CI_PIPELINE_IID}` (npm semver pre-release).
- Publishes to the GitLab Package Registry with `--tag dev` so the `latest` dist-tag is never modified.

---

## [0.2.0] — 2026-01 (initial public release)

### Added

- `KubernetesProvider` with `agent-sandbox` and `raw` backend modes.
- `KubernetesSandbox` extending `BaseSandbox` (execute, upload, download).
- `AgentSandboxBackend`: fetch-based HTTP client for the `kubernetes-sigs/agent-sandbox` sandbox-router.
- `RawK8sBackend`: direct Pod management via `@kubernetes/client-node`.
- `SandboxRouterClient`: typed HTTP wrapper for the sandbox-router REST API.
- Deny-all `NetworkPolicy` per sandbox (raw mode, opt-out via `blockNetwork: false`).
- Per-sandbox namespace isolation (`namespacePerSandbox`).
- Hardened Pod security context defaults (`runAsNonRoot`, `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, `seccompProfile: RuntimeDefault`).
- Full file I/O: `uploadFiles` / `downloadFiles` via tar-over-exec (raw) and base64-encoded exec (agent-sandbox).
- 65 unit tests.

[0.4.0]: https://github.com/bitkaio/langchain-kubernetes/compare/typescript/v0.3.0...typescript/v0.4.0
[0.3.0]: https://github.com/bitkaio/langchain-kubernetes/compare/typescript/v0.2.0...typescript/v0.3.0
[0.2.0]: https://github.com/bitkaio/langchain-kubernetes/releases/tag/typescript/v0.2.0
