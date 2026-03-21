# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added — both packages

#### Labels and thread-ID support
- New label prefix `langchain-kubernetes.bitkaio.com/` applied to every managed resource.
- `get_or_create()` / `getOrCreate()` now accept `thread_id` / `threadId`: performs a Kubernetes label-selector lookup before creating a new sandbox, making repeated calls for the same conversation idempotent.
- New `labels` / `callLabels` per-call parameter: merged labels are applied to the sandbox resource (auto-prefixed).
- New `default_labels` / `defaultLabels` provider-level config: applied to every sandbox created by this provider instance.
- `find_by_thread_id()` / `findByThreadId()`: look up an existing sandbox by thread identifier without creating one.
- Label sanitization: thread IDs that violate Kubernetes label value constraints (>63 chars, invalid characters) are SHA-256 hashed (12 hex chars); the original is stored in the `langchain-kubernetes.bitkaio.com/thread-id-original` annotation.
- Label merging priority: `managed-by` < `default_labels` < per-call `labels` < `thread-id`.

#### TTL and auto-cleanup
- New `ttl_seconds` / `ttlSeconds` parameter on `get_or_create()` and provider config: sets the `langchain-kubernetes.bitkaio.com/ttl-seconds` and `langchain-kubernetes.bitkaio.com/created-at` annotations at creation time.
- New `ttl_idle_seconds` / `ttlIdleSeconds` parameter: sets `langchain-kubernetes.bitkaio.com/ttl-idle-seconds`; `execute()` updates `langchain-kubernetes.bitkaio.com/last-activity` after each call (fire-and-forget, never blocks or raises).
- New `cleanup(max_idle_seconds?)` / `cleanup(maxIdleSeconds?)` method: deletes sandboxes that have exceeded their absolute or idle TTL.
- New `stats(idle_threshold_seconds?)` / `stats(idleThresholdSeconds?)` method: returns aggregate `ProviderStats` (total, running, warm, idle, thread_ids / threadIds).
- New `list()` method: queries the Kubernetes API directly; supports filtering by `thread_id`, `labels`, and `status`; returns paginated `SandboxListResponse` with a `cursor` for continuation.
- New `pool_status()` / `poolStatus()` method: returns `WarmPoolStatus` (available, active, total, target).

#### KubernetesSandboxManager
- New `KubernetesSandboxManager` class: high-level wrapper with a LangGraph-compatible `backend_factory` / `backendFactory` callable.
- `backend_factory` / `backendFactory` extracts `thread_id` from the LangGraph `RunnableConfig`'s `configurable` dict, calls `get_or_create()` with the configured TTL and label settings, and caches the result in-process.
- `abackend_factory()` / `abackendFactory()`: async variant.
- `get_sandbox(thread_id)` / `getSandbox(threadId)`: cache lookup without creation.
- `shutdown()` / `shutdown()`: deletes all tracked sandboxes; errors are logged but do not raise.
- `ashutdown()` (Python) / `Symbol.asyncDispose` (TypeScript): async cleanup; supports `async with` / `await using`.
- Context manager support: `with KubernetesSandboxManager(...) as m:` / `async with ...` (Python); `await using` (TypeScript 5.2+).
- Thread-safe in-process cache protected by `threading.Lock` (Python).

#### Warm pool integration
- **agent-sandbox mode**: new `warm_pool_name` / `warmPoolName` config field — claims sandboxes from a named `SandboxWarmPool` CRD instead of cold-creating them.
- **raw mode**: new `warm_pool_size` / `warmPoolSize` config field — provider pre-creates idle Pods labelled `pool-status=warm`; `get_or_create()` claims a warm Pod (patches label to `pool-status=active`) before falling back to cold creation; `delete()` schedules background replenishment.
- Warm pool is initialised lazily on the first `get_or_create()` call.

#### New config fields (both packages)
- `default_labels` / `defaultLabels` — applied to every sandbox.
- `ttl_seconds` / `ttlSeconds` — default absolute TTL.
- `ttl_idle_seconds` / `ttlIdleSeconds` — default idle TTL.
- `warm_pool_size` / `warmPoolSize` — raw mode warm pool size.
- `warm_pool_name` / `warmPoolName` — agent-sandbox mode warm pool CRD name.
- `kube_api_url` / `kubeApiUrl` — Kubernetes API URL for CRD operations in agent-sandbox mode.
- `kube_token` / `kubeToken` — Bearer token for Kubernetes API (auto-reads in-cluster service account token if unset).

#### New exported types
- `SandboxInfo` — metadata entry returned by `list()`.
- `SandboxListResponse` — paginated list result (`sandboxes` + `cursor`).
- `CleanupResult` — result of `cleanup()` (`deleted` IDs + `kept` count).
- `WarmPoolStatus` — pool state snapshot (`available`, `active`, `total`, `target`).
- `ProviderStats` — aggregate statistics.
- `GetOrCreateOptions` / `KubernetesSandboxManagerOptions` (TypeScript) — option bag types.

### Added — Python package

- `langchain_kubernetes/_labels.py`: label/annotation constants, `sanitize_label_value()`, `build_labels()`, `build_ttl_annotations()`, `thread_id_selector()`, `warm_pool_selector()`.
- `langchain_kubernetes/_types.py`: `SandboxInfo`, `SandboxListResponse`, `CleanupResult`, `WarmPoolStatus`, `ProviderStats` dataclasses.
- `langchain_kubernetes/_k8s_http.py`: stdlib `urllib`-based Kubernetes HTTP client for agent-sandbox mode — reads in-cluster service-account token automatically; no hard `kubernetes` package dependency.
- `langchain_kubernetes/manager.py`: `KubernetesSandboxManager` and `_extract_thread_id()` helper.
- `langchain_kubernetes/provider.py` — major additions: `get_or_create()` with thread-ID lookup, `find_by_thread_id()`, `list()`, `delete()` with warm-pool replenishment, `cleanup()`, `stats()`, `pool_status()`; async variants for all methods.
- `langchain_kubernetes/backends/raw.py` — new `find_by_thread_id()`, `claim_warm_pod()` class methods; `_update_last_activity()` fire-and-forget patch; `create()` accepts `extra_labels`, `extra_annotations`, `ttl_idle_seconds`.
- `langchain_kubernetes/backends/raw_manifests.py` — `build_pod_manifest()` accepts `extra_labels` and `extra_annotations`.
- `langchain_kubernetes/backends/agent_sandbox.py` — `execute()` invokes optional `activity_callback` (fire-and-forget) for idle-TTL tracking.
- Exports in `__init__.py`: `KubernetesSandboxManager`, `SandboxInfo`, `SandboxListResponse`, `CleanupResult`, `WarmPoolStatus`, `ProviderStats`.
- New unit test files: `tests/unit/test_thread_id.py`, `tests/unit/test_cleanup.py`, `tests/unit/test_manager.py`, `tests/unit/test_warm_pool.py`, `tests/unit/test_list_filters.py`.
- Total unit tests: **244** (up from 181).

### Added — TypeScript package

- `src/labels.ts`: label/annotation constants, `sanitizeLabelValue()`, `buildLabels()`, `buildTtlAnnotations()`, `threadIdSelector()`, `warmPoolSelector()`.
- `src/manager.ts`: `KubernetesSandboxManager`, `KubernetesSandboxManagerOptions`, `extractThreadId()`.
- `src/sandbox.ts` — `setActivityCallback(cb)`: registers a fire-and-forget callback invoked after each `execute()` for idle-TTL tracking.
- `src/provider.ts` — major additions: `getOrCreate()` accepts `GetOrCreateOptions`; `findByThreadId()`, `list()`, `delete()`, `cleanup()`, `stats()`, `poolStatus()`; all new interfaces exported.
- `src/backends/raw.ts` — `create()` accepts `sandboxId?`, `extraLabels?`, `extraAnnotations?`, `ttlIdleSeconds?`; `execute()` patches `last-activity` annotation fire-and-forget when `ttlIdleSeconds` is set; exported `loadK8sClients()` function.
- `src/backends/raw-manifests.ts` — `buildPodManifest()` accepts `extraLabels?`, `extraAnnotations?`.
- `src/backends/agent-sandbox.ts` — `create()` accepts `_extraLabels?`, `_extraAnnotations?`.
- `src/router-client.ts` — `listSandboxClaims(labelSelector?)` and `patchSandboxClaim(name, labels, annotations)` methods added.
- `src/config.ts` — added `defaultLabels`, `ttlSeconds`, `ttlIdleSeconds`, `warmPoolSize`, `warmPoolName` fields to `KubernetesProviderConfig`.
- `src/index.ts` — exports `KubernetesSandboxManager`, `KubernetesSandboxManagerOptions`, `CleanupResult`, `WarmPoolStatus`, `ProviderStats`, `GetOrCreateOptions`.
- New unit test files: `tests/unit/thread-id.test.ts`, `tests/unit/cleanup.test.ts`, `tests/unit/manager.test.ts`, `tests/unit/warm-pool.test.ts`.
- Total unit tests: **124** (up from 65).

### Added — Documentation

- `docs/warm-pool.yaml`: annotated `SandboxWarmPool` CRD + raw-mode provider config examples + required RBAC.
- `docs/reaper-cronjob.yaml`: CronJob + ServiceAccount + ClusterRole for scheduled TTL cleanup.
- `docs/langgraph-integration.md`: full Python and TypeScript integration guide covering `KubernetesSandboxManager`, per-thread lookup, TTL annotations, and local development setup.
- `docs/openshift.md`: OpenShift-specific notes — SCC options (`anyuid`, `restricted-v2`, custom), NetworkPolicy CNI compatibility, OLM installation, LimitRange guidance.
- Root `README.md`: "Per-conversation sandboxes", "Warm pool — sub-second startup", "For regulated industries" sections; feature comparison table vs Daytona / Modal / Runloop.
- `python/README.md`: `KubernetesSandboxManager` API table; full `provider` API reference for new methods; new config fields table; warm pool configuration examples.
- `typescript/README.md`: `KubernetesSandboxManager` usage; `GetOrCreateOptions`, `SandboxListResponse`, `CleanupResult`, `ProviderStats`, `WarmPoolStatus` interface docs; new config fields table; warm pool configuration examples.

### Changed

- `get_or_create()` / `getOrCreate()` now accepts an options object (`GetOrCreateOptions` in TypeScript, keyword arguments in Python) in addition to the legacy `sandbox_id` / `sandboxId` positional string — fully backward-compatible.
- `list()` return type changed from a plain list (`list[SandboxBackendProtocol]`) to `SandboxListResponse` with a `.sandboxes` field and optional `.cursor`. **Breaking change** for direct callers of `provider.list()`.
- `RawK8sBackend.create()` signature extended with optional `sandboxId`, `extraLabels`, `extraAnnotations`, `ttlIdleSeconds` parameters — fully backward-compatible.
- `AgentSandboxBackend.create()` signature extended with optional `_extraLabels`, `_extraAnnotations` parameters — fully backward-compatible.
- `buildPodManifest()` extended with optional `extraLabels`, `extraAnnotations` parameters — fully backward-compatible.

### Fixed

- `router-client.ts`: renamed inner `contentType` variable in response parsing to `respContentType` to resolve duplicate identifier compiler error.
- `raw-manifests.ts`: corrected always-truthy expression in annotation conditional (`{} || undefined` → proper ternary).

### Fixed — bugs discovered during integration testing

#### Phantom sandboxes (race condition in `KubernetesSandboxManager`) — both packages

- **Root cause**: `_get_or_create_cached` (Python) / `abackendFactory` (TypeScript) released the cache lock before calling `get_or_create`. When the LangGraph Platform server dispatched multiple concurrent async workers for the same `thread_id` (e.g., model node + tool node in the same run), all workers independently missed the cache and each provisioned its own `SandboxClaim` / Pod — leaving N−1 orphaned sandboxes per run.
- **Python fix**: Added `_thread_locks: dict[str, threading.Lock]` alongside the existing global `_lock`. A per-thread-id lock is acquired before provisioning and held until the sandbox is stored in the cache. Concurrent callers for the same `thread_id` wait on the lock and return the already-cached sandbox when they acquire it.
- **TypeScript fix**: Added `_pending: Map<string, Promise<KubernetesSandbox>>`. Concurrent callers that race past the cache-hit check all find the same in-flight `Promise` in `_pending` and await it — ensuring only one `getOrCreate` network call is made per thread_id at any time.

#### K8s API calls unconditional in agent-sandbox mode — both packages

- **Root cause**: The thread_id label lookup (`_find_by_thread_id_agent_sandbox` / `findByThreadIdAgentSandbox`) and post-creation label patches (`_patch_sandbox_claim` / `patchSandboxClaim`) always attempted direct Kubernetes API calls. In the common case of agent-sandbox mode accessed through an external sandbox-router (without direct cluster API access), these calls failed on every request — emitting a WARNING log flood and (combined with the race condition) causing phantom sandboxes.
- **Design clarification**: In agent-sandbox mode, the sandbox-router is the designed external API surface. Direct K8s API access is an *optional enhancement* for cross-process reconnection (writing thread_id labels to SandboxClaims so a restarted process can find existing sandboxes). It is not required for correct single-process operation.
- **Python fix**: Added `is_k8s_api_configured(api_url, token_override)` in `_k8s_http.py`. Returns `True` if `kube_api_url` or `kube_token` are explicitly set, or if the in-cluster service account token exists. All three K8s operations (`_find_by_thread_id_agent_sandbox`, `_patch_sandbox_claim`, `_patch_claim_last_activity`) are now gated on this check and silently skipped (DEBUG log) when not configured. WARNING logs are reserved for cases where K8s API IS configured but the request fails.
- **TypeScript fix**: Added `isK8sApiConfigured(kubeApiUrl?, kubeToken?)` exported from `router-client.ts`. Returns `True` if either explicit config field is set or `KUBERNETES_SERVICE_HOST` env var is present (the Kubernetes in-cluster detection standard). `findByThreadIdAgentSandbox` and both `patchSandboxClaim` call sites in `provider.ts` are gated on this check.

#### `_raise_clear_agent_sandbox_error` swallowing `blockbuster.BlockingError` — Python only

- **Root cause**: The connectivity error check matched any exception whose message contained `"connect"`. `blockbuster.BlockingError: Blocking call to socket.socket.connect` — a dev-server enforcement error — was being caught and re-raised as a misleading `RuntimeError: Cannot reach the sandbox-router`, completely hiding the real cause.
- **Fix**: Added an early guard: if `"blocking call"` is in the lowercased message, re-raise the original exception immediately before any keyword matching.

#### `backend_factory` blocking the asyncio event loop on cache miss — Python only

- **Root cause**: The sync `backend_factory` callable called `_get_or_create_cached` synchronously. When called from inside a running asyncio event loop (e.g., from `abackend_factory` before the run_in_executor fix, or directly from user code), the blocking `SandboxClient.__enter__()` I/O would block the event loop — degrading ASGI servers and being caught by blockbuster in dev mode.
- **Fix**: `_get_or_create_cached` now detects a running event loop on cache miss via `asyncio.get_running_loop()` and raises a clear `RuntimeError` listing three actionable alternatives (`abackend_factory`, `asyncio.to_thread`, synchronous pre-warm before loop start). `abackend_factory` was updated to use `asyncio.get_running_loop()` instead of the deprecated `asyncio.get_event_loop()`.

#### CI — manual dev-publish jobs

- Added `python:dev-publish` and `typescript:dev-publish` manual GitLab CI jobs in a new `dev-publish` stage.
- Triggered via the GitLab UI play button or API from any branch; never run automatically.
- Version scheme: `{base}.dev{CI_PIPELINE_IID}` (Python PEP 440) / `{base}-dev.{CI_PIPELINE_IID}` (npm semver pre-release).
- Publishes only to the GitLab Package Registry; TypeScript is tagged with `--tag dev` so the `latest` npm dist-tag is never modified.
- Updated `workflow.rules` to allow pipelines on any branch so manual jobs are accessible from feature branches.

---

## [0.0.1] — 2025-01 (initial release)

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
