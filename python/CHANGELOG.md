# Changelog — langchain-kubernetes (Python)

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Changed

#### KubernetesSandboxManager — streaming-compatible two-node architecture

- `create_agent()` now builds a two-node `START → setup → agent → END` graph instead of a single-node graph. The deepagent is compiled once as a proper LangGraph subgraph node, which enables real-time streaming of LLM tokens and tool calls from LangGraph Studio and the LangGraph Platform.
- New `_sandbox_by_thread: dict[str, KubernetesSandbox]` instance cache — the setup node populates it before the agent subgraph runs; the backend factory reads from it synchronously during agent execution.
- New `_make_backend_factory()` private method: returns a sync callable that resolves the current thread's sandbox from `_sandbox_by_thread` via `langchain_core.runnables.config.ensure_config()`. Raises `RuntimeError` when `thread_id` is absent or the thread has no cached sandbox.
- New `create_setup_node(*, state_sandbox_key="sandbox_id")` public method: returns an async LangGraph node that acquires (or reconnects) the sandbox, stores it in the cache, and writes the sandbox ID back to state when it changed. Intended to be wired before the deepagent subgraph in custom `StateGraph` builds.
- `create_agent_node()` is unchanged and kept for backward compatibility.

---

## [0.3.0] — 2026-03-21

### Added

#### KubernetesSandboxManager

- New `KubernetesSandboxManager` class: stateless high-level wrapper for building DeepAgents-powered applications. Holds no sandbox cache and no locks — all sandbox-to-conversation binding lives in LangGraph graph state.
- `create_agent(model, *, checkpointer=None)`: returns a compiled LangGraph+DeepAgents graph. Stores `sandbox_id` in graph state; the checkpointer (or LangGraph Platform) persists it between runs so each thread reconnects to the same sandbox automatically.
- `create_agent_node(model)`: returns a single async node function for embedding in a larger `StateGraph`.
- `get_or_reconnect(sandbox_id)`: lower-level helper; delegates to `provider.aget_or_create(sandbox_id=...)`.
- `shutdown()`: deletes all sandboxes created by this provider instance; errors are logged but do not raise.
- `ashutdown()`: async cleanup variant.
- `langchain_kubernetes/manager.py`: new module containing `KubernetesSandboxManager`.

#### TTL and auto-cleanup

- New `ttl_seconds` and `ttl_idle_seconds` `KubernetesProviderConfig` fields: set `langchain-kubernetes.bitkaio.com/ttl-seconds`, `created-at`, and `ttl-idle-seconds` annotations at creation time.
- `execute()` updates `langchain-kubernetes.bitkaio.com/last-activity` annotation after each call (fire-and-forget, never blocks or raises).
- New `cleanup(max_idle_seconds=None)` method on `KubernetesProvider`: deletes sandboxes that have exceeded their absolute or idle TTL.
- New `stats(idle_threshold_seconds=300)` method: returns aggregate `ProviderStats` (total, running, warm, idle).
- New `list()` method: queries the Kubernetes API; supports filtering by `labels` and `status`; returns `SandboxListResponse`.
- New `pool_status()` method: returns `WarmPoolStatus` (available, active, total, target).
- Async variants for all provider methods: `aget_or_create()`, `alist()`, `adelete()`, `acleanup()`, `astats()`.

#### Warm pool integration

- **agent-sandbox mode**: new `warm_pool_name` config field — claims sandboxes from a named `SandboxWarmPool` CRD instead of cold-creating them.
- **raw mode**: new `warm_pool_size` config field — provider pre-creates idle Pods labelled `pool-status=warm`; `get_or_create()` claims a warm Pod (patches label to `pool-status=active`) before falling back to cold creation; `delete()` triggers background replenishment.

#### Optional K8s API access in agent-sandbox mode

- `is_k8s_api_configured()` in `langchain_kubernetes/_k8s_http.py`: returns `True` when direct K8s API access is available (explicit `kube_api_url` / `kube_token` config, or in-cluster service account token). When configured, `reconnect()` verifies sandbox existence before returning; otherwise returns optimistically.

#### New config fields (`KubernetesProviderConfig`)

- `default_labels` — applied to every sandbox.
- `ttl_seconds` — default absolute TTL.
- `ttl_idle_seconds` — default idle TTL.
- `warm_pool_size` — raw mode warm pool target size.
- `warm_pool_name` — agent-sandbox mode warm pool CRD name.
- `kube_api_url` — Kubernetes API URL for optional direct K8s API access in agent-sandbox mode.
- `kube_token` — Bearer token for Kubernetes API (auto-reads in-cluster service account token if unset).

#### New exported types

- `SandboxInfo` — metadata entry returned by `list()`.
- `SandboxListResponse` — list result with `.sandboxes` and optional `.cursor`.
- `CleanupResult` — result of `cleanup()` (`.deleted` IDs + `.kept` count).
- `WarmPoolStatus` — pool state snapshot (`.available`, `.active`, `.total`, `.target`).
- `ProviderStats` — aggregate statistics.

#### New internal modules

- `langchain_kubernetes/_labels.py`: label/annotation constants, `sanitize_label_value()`, `build_labels()`, `build_ttl_annotations()`, `warm_pool_selector()`.
- `langchain_kubernetes/_types.py`: `SandboxInfo`, `SandboxListResponse`, `CleanupResult`, `WarmPoolStatus`, `ProviderStats` dataclasses.
- `langchain_kubernetes/_k8s_http.py`: stdlib `urllib`-based Kubernetes HTTP client for agent-sandbox mode.
- `langchain_kubernetes/provider.py`: `get_or_create()` with reconnect support, `list()`, `delete()` with warm-pool replenishment, `cleanup()`, `stats()`, `pool_status()`.

### Changed

- `get_or_create()` now accepts keyword arguments including `sandbox_id` for reconnection, `labels`, `ttl_seconds`, `ttl_idle_seconds` — fully backward-compatible.
- `list()` return type is now `SandboxListResponse` with a `.sandboxes` field. **Breaking change** for direct callers of `provider.list()`.
- `RawK8sBackend.create()` signature extended with optional `sandbox_id`, `extra_labels`, `extra_annotations`, `ttl_idle_seconds` parameters — fully backward-compatible.

### Fixed

- `_raise_clear_agent_sandbox_error` no longer misidentifies `blockbuster.BlockingError` as a connectivity failure — the original exception is re-raised immediately when `"blocking call"` is detected in the message.

### CI

- Added `python:dev-publish` manual GitLab CI job in a new `dev-publish` stage; triggered via the GitLab UI or API from any branch, never run automatically.
- Version scheme: `{base}.dev{CI_PIPELINE_IID}` (PEP 440 pre-release).
- Publishes to the GitLab Package Registry only.

---

## [0.2.0] — 2026-01 (initial public release)

### Added

- `KubernetesProvider` with `agent-sandbox` and `raw` backend modes.
- `KubernetesSandbox` extending `BaseSandbox` (execute, upload, download).
- `AgentSandboxBackend`: wraps `k8s-agent-sandbox` `SandboxClient`.
- `RawK8sBackend`: direct Pod management via the `kubernetes` Python client.
- Deny-all `NetworkPolicy` per sandbox (raw mode, opt-out via `block_network=False`).
- Per-sandbox namespace isolation (`namespace_per_sandbox`).
- Hardened Pod security context defaults (`runAsNonRoot`, `allowPrivilegeEscalation: false`, `capabilities.drop: [ALL]`, `seccompProfile: RuntimeDefault`).
- Full file I/O: `upload_files` / `download_files` via tar-over-exec (raw) and `SandboxClient` file methods (agent-sandbox).
- 181 unit tests.

[0.3.0]: https://github.com/bitkaio/langchain-kubernetes/compare/python/v0.2.0...python/v0.3.0
[0.2.0]: https://github.com/bitkaio/langchain-kubernetes/releases/tag/python/v0.2.0
