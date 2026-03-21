# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added — both packages

#### KubernetesSandboxManager

- New `KubernetesSandboxManager` class: stateless high-level wrapper for building DeepAgents-powered applications. Holds no sandbox cache and no locks — all sandbox-to-conversation binding lives in LangGraph graph state.
- `create_agent(model, *, checkpointer=None)` / `createAgent(model, { checkpointer? })`: returns a compiled LangGraph+DeepAgents graph. Stores `sandbox_id` / `sandboxId` in graph state; the checkpointer (or LangGraph Platform) persists it between runs so each thread reconnects to the same sandbox automatically.
- `create_agent_node(model)` / `createAgentNode(model)`: returns a single async node function for embedding in a larger `StateGraph`.
- `get_or_reconnect(sandbox_id)` / `getOrReconnect(sandboxId)`: lower-level helper; delegates to `provider.get_or_create(sandbox_id=...)`.
- `shutdown()` / `shutdown()`: deletes all sandboxes created by this provider instance; errors are logged but do not raise.
- `ashutdown()` (Python): async cleanup variant.

#### TTL and auto-cleanup

- New `ttl_seconds` / `ttlSeconds` and `ttl_idle_seconds` / `ttlIdleSeconds` provider config fields: set `langchain-kubernetes.bitkaio.com/ttl-seconds`, `created-at`, and `ttl-idle-seconds` annotations at creation time.
- `execute()` updates `langchain-kubernetes.bitkaio.com/last-activity` annotation after each call (fire-and-forget, never blocks or raises).
- New `cleanup(max_idle_seconds?)` / `cleanup(maxIdleSeconds?)` method: deletes sandboxes that have exceeded their absolute or idle TTL.
- New `stats(idle_threshold_seconds?)` / `stats(idleThresholdSeconds?)` method: returns aggregate `ProviderStats` (total, running, warm, idle).
- New `list()` method: queries the Kubernetes API; supports filtering by `labels` and `status`; returns `SandboxListResponse`.
- New `pool_status()` / `poolStatus()` method: returns `WarmPoolStatus` (available, active, total, target).

#### Warm pool integration

- **agent-sandbox mode**: new `warm_pool_name` / `warmPoolName` config field — claims sandboxes from a named `SandboxWarmPool` CRD instead of cold-creating them.
- **raw mode**: new `warm_pool_size` / `warmPoolSize` config field — provider pre-creates idle Pods labelled `pool-status=warm`; `get_or_create()` claims a warm Pod (patches label to `pool-status=active`) before falling back to cold creation; `delete()` triggers background replenishment.

#### Optional K8s API access in agent-sandbox mode

- `is_k8s_api_configured()` / `isK8sApiConfigured()`: returns `True` when direct K8s API access is available (explicit `kube_api_url` / `kube_token` config, or in-cluster service account token). When configured, `reconnect()` verifies sandbox existence via the K8s API before returning; when not configured, it returns optimistically and any error surfaces on the first `execute()` call.

#### New config fields

- `default_labels` / `defaultLabels` — applied to every sandbox.
- `ttl_seconds` / `ttlSeconds` — default absolute TTL.
- `ttl_idle_seconds` / `ttlIdleSeconds` — default idle TTL.
- `warm_pool_size` / `warmPoolSize` — raw mode warm pool size.
- `warm_pool_name` / `warmPoolName` — agent-sandbox mode warm pool CRD name.
- `kube_api_url` / `kubeApiUrl` — Kubernetes API URL for optional K8s API access in agent-sandbox mode.
- `kube_token` / `kubeToken` — Bearer token for Kubernetes API (auto-reads in-cluster service account token if unset).

#### New exported types

- `SandboxInfo` — metadata entry returned by `list()`.
- `SandboxListResponse` — list result with `.sandboxes`.
- `CleanupResult` — result of `cleanup()` (`deleted` IDs + `kept` count).
- `WarmPoolStatus` — pool state snapshot (`available`, `active`, `total`, `target`).
- `ProviderStats` — aggregate statistics.

### Added — Python package

- `langchain_kubernetes/_labels.py`: label/annotation constants, `sanitize_label_value()`, `build_labels()`, `build_ttl_annotations()`, `warm_pool_selector()`.
- `langchain_kubernetes/_types.py`: `SandboxInfo`, `SandboxListResponse`, `CleanupResult`, `WarmPoolStatus`, `ProviderStats` dataclasses.
- `langchain_kubernetes/_k8s_http.py`: stdlib `urllib`-based Kubernetes HTTP client for agent-sandbox mode; `is_k8s_api_configured()`.
- `langchain_kubernetes/manager.py`: `KubernetesSandboxManager` with `create_agent()`, `create_agent_node()`, `get_or_reconnect()`.
- `langchain_kubernetes/provider.py` — `get_or_create()` with reconnect support, `list()`, `delete()` with warm-pool replenishment, `cleanup()`, `stats()`, `pool_status()`; async variants for all methods.
- Exports in `__init__.py`: `KubernetesSandboxManager`, `SandboxInfo`, `SandboxListResponse`, `CleanupResult`, `WarmPoolStatus`, `ProviderStats`.

### Added — TypeScript package

- `src/labels.ts`: label/annotation constants, `sanitizeLabelValue()`, `buildLabels()`, `buildTtlAnnotations()`, `warmPoolSelector()`.
- `src/manager.ts`: `KubernetesSandboxManager`, `KubernetesSandboxManagerOptions` with `createAgent()`, `createAgentNode()`, `getOrReconnect()`.
- `src/router-client.ts` — `listSandboxClaims(labelSelector?)` method; `isK8sApiConfigured()` export.
- `src/config.ts` — added `defaultLabels`, `ttlSeconds`, `ttlIdleSeconds`, `warmPoolSize`, `warmPoolName`, `kubeApiUrl`, `kubeToken` fields.
- `src/index.ts` — exports `KubernetesSandboxManager`, `KubernetesSandboxManagerOptions`, `CleanupResult`, `WarmPoolStatus`, `ProviderStats`.

### Added — Documentation

- `docs/warm-pool.yaml`: annotated `SandboxWarmPool` CRD + raw-mode warm pool examples + required RBAC.
- `docs/reaper-cronjob.yaml`: CronJob + ServiceAccount + ClusterRole for scheduled TTL cleanup.
- `docs/openshift.md`: OpenShift-specific notes — SCC options, NetworkPolicy CNI compatibility, OLM installation, LimitRange guidance.
- Root `README.md`: "Multi-turn: persistent sandbox per conversation", "Warm pool — sub-second startup", "For regulated industries" sections; feature comparison table vs Daytona / Modal / Runloop.
- `python/README.md`: `KubernetesSandboxManager` API; full provider method reference; config fields table; warm pool configuration examples.
- `typescript/README.md`: `KubernetesSandboxManager` usage; interface docs; config fields table; warm pool configuration examples.

### Changed

- `get_or_create()` / `getOrCreate()` now accepts an options object (keyword arguments in Python, options bag in TypeScript) including `sandbox_id` / `sandboxId` for reconnection — fully backward-compatible.
- `list()` return type is now `SandboxListResponse` with a `.sandboxes` field. **Breaking change** for direct callers of `provider.list()`.
- `RawK8sBackend.create()` signature extended with optional `sandboxId`, `extraLabels`, `extraAnnotations`, `ttlIdleSeconds` parameters — fully backward-compatible.
- `buildPodManifest()` extended with optional `extraLabels`, `extraAnnotations` parameters — fully backward-compatible.

### Fixed

- `router-client.ts`: renamed inner `contentType` variable in response parsing to `respContentType` to resolve duplicate identifier compiler error.
- `raw-manifests.ts`: corrected always-truthy expression in annotation conditional (`{} || undefined` → proper ternary).
- Python: `_raise_clear_agent_sandbox_error` no longer misidentifies `blockbuster.BlockingError` as a connectivity failure — the original exception is re-raised immediately when `"blocking call"` is detected in the message.

### CI

- Added `python:dev-publish` and `typescript:dev-publish` manual GitLab CI jobs in a new `dev-publish` stage; triggered via the GitLab UI or API from any branch, never run automatically.
- Version scheme: `{base}.dev{CI_PIPELINE_IID}` (Python PEP 440) / `{base}-dev.{CI_PIPELINE_IID}` (npm semver pre-release).
- Publishes to the GitLab Package Registry only; TypeScript tagged `--tag dev` so the `latest` npm dist-tag is never modified.

---

## [0.0.1] — 2026-01 (initial release)

### Added

- `KubernetesProvider` with `agent-sandbox` and `raw` backend modes.
- `KubernetesSandbox` extending `BaseSandbox` (execute, upload, download).
- `AgentSandboxBackend`: fetch-based client for the `kubernetes-sigs/agent-sandbox` router.
- `RawK8sBackend`: direct Pod management via `@kubernetes/client-node` (TypeScript) / `kubernetes` (Python).
- `SandboxRouterClient`: typed HTTP wrapper for the sandbox-router REST API.
- Deny-all `NetworkPolicy` per sandbox (raw mode, opt-out via `block_network=False`).
- Per-sandbox namespace isolation (`namespace_per_sandbox` / `namespacePerSandbox`).
- Hardened Pod security context defaults (`runAsNonRoot`, `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, `seccompProfile: RuntimeDefault`).
- Full file I/O: `uploadFiles` / `downloadFiles` via tar-over-exec (raw) and base64-encoded exec (agent-sandbox).
- 65 unit tests (TypeScript) and 181 unit tests (Python).

[Unreleased]: https://github.com/bitkaio/langchain-kubernetes/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/bitkaio/langchain-kubernetes/releases/tag/v0.0.1
