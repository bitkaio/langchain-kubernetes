"""KubernetesProvider: manages the lifecycle of KubernetesSandbox instances."""

from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from typing import Any

from deepagents.backends.protocol import SandboxBackendProtocol

from langchain_kubernetes._labels import (
    ANN_CREATED_AT,
    ANN_LAST_ACTIVITY,
    ANN_TTL_IDLE_SECONDS,
    ANN_TTL_SECONDS,
    LABEL_POOL_STATUS,
    LABEL_PREFIX,
    LABEL_THREAD_ID,
    MANAGED_SELECTOR,
    POOL_STATUS_ACTIVE,
    POOL_STATUS_WARM,
    build_labels,
    build_ttl_annotations,
    now_iso,
    sanitize_label_value,
    thread_id_selector,
    warm_pool_selector,
)
from langchain_kubernetes._provider_base import (
    SandboxNotFoundError,
    SandboxProvider,
)
from langchain_kubernetes._types import (
    CleanupResult,
    ProviderStats,
    SandboxInfo,
    SandboxListResponse,
    WarmPoolStatus,
)
from langchain_kubernetes.backends.agent_sandbox import AgentSandboxBackend
from langchain_kubernetes.backends.protocol import KubernetesBackendProtocol
from langchain_kubernetes.config import KubernetesProviderConfig
from langchain_kubernetes.sandbox import KubernetesSandbox

logger = logging.getLogger(__name__)

# CRD constants for agent-sandbox SandboxClaim resources
_CLAIM_API_GROUP = "extensions.agents.x-k8s.io"
_CLAIM_API_VERSION = "v1alpha1"
_CLAIM_PLURAL = "sandboxclaims"


class KubernetesProvider(SandboxProvider):
    """Lifecycle manager for Kubernetes-based sandboxes.

    Supports two backend modes selected via ``config.mode``:

    - ``"agent-sandbox"`` — provisions Sandbox CRs via the
      ``kubernetes-sigs/agent-sandbox`` controller. Requires the controller,
      CRDs, sandbox-router, and at least one ``SandboxTemplate`` to be deployed.
    - ``"raw"`` — directly creates ephemeral Pods. Works on any cluster with no
      additional infrastructure.

    Active backends are tracked in-process. Calling :meth:`delete` triggers
    backend cleanup (Sandbox CR deletion or Pod deletion). Backends created in a
    previous process are not visible to in-process tracking but *are* visible to
    :meth:`list` which queries the Kubernetes API directly.

    Args:
        config: Provider configuration. For agent-sandbox mode, ``template_name``
            is required. For raw mode, image and resource fields are used.
    """

    def __init__(self, config: KubernetesProviderConfig) -> None:
        self._config = config
        # Maps sandbox_id -> active backend
        self._active_backends: dict[str, KubernetesBackendProtocol] = {}
        # Maps thread_id -> sandbox_id for fast lookup
        self._thread_id_map: dict[str, str] = {}
        # Warm-pool initialisation flag
        self._warm_pool_initialised = False
        self._warm_pool_lock = threading.Lock()

    # ------------------------------------------------------------------
    # SandboxProvider: sync interface
    # ------------------------------------------------------------------

    def get_or_create(
        self,
        *,
        sandbox_id: str | None = None,
        thread_id: str | None = None,
        labels: dict[str, str] | None = None,
        ttl_seconds: int | None = None,
        ttl_idle_seconds: int | None = None,
        **kwargs: Any,
    ) -> KubernetesSandbox:
        """Return an existing sandbox or create a new one.

        When *thread_id* is provided the provider first checks whether a
        sandbox already exists for that thread (via an in-cluster label
        lookup).  If found, it reconnects; if not found, a new sandbox is
        created with the thread-id label set.

        When *sandbox_id* is provided (and *thread_id* lookup yields nothing),
        the provider checks its in-process cache, then falls back to
        reconnecting from the cluster.

        Label merging order (later wins):

        1. ``managed-by`` (always set).
        2. Config ``default_labels`` (auto-prefixed).
        3. Per-call ``labels`` (auto-prefixed).
        4. ``thread-id`` (if provided).

        Args:
            sandbox_id: Existing sandbox ID to reconnect to. Ignored when a
                *thread_id* lookup succeeds.
            thread_id: Thread / conversation identifier. Triggers an
                in-cluster lookup before any creation.
            labels: Per-call labels merged onto the new sandbox (keys are
                auto-prefixed). No effect on reconnect.
            ttl_seconds: Absolute TTL from creation (overrides config default).
            ttl_idle_seconds: Idle TTL from last execute() (overrides config).
            **kwargs: Unused; present for interface compatibility.

        Returns:
            :class:`~langchain_kubernetes.sandbox.KubernetesSandbox` instance.

        Raises:
            SandboxNotFoundError: When *sandbox_id* is given, the thread_id
                lookup fails, and the ID is not in cache.
            ImportError: When the required backend package is not installed.
            TimeoutError: If the sandbox does not become ready in time.
            RuntimeError: On sandbox creation failure.
        """
        # ------------------------------------------------------------------
        # Lazy warm-pool init (raw mode only)
        # ------------------------------------------------------------------
        if self._config.mode == "raw" and self._config.warm_pool_size > 0:
            self._ensure_warm_pool()

        # ------------------------------------------------------------------
        # Effective TTL values
        # ------------------------------------------------------------------
        eff_ttl = ttl_seconds if ttl_seconds is not None else self._config.ttl_seconds
        eff_idle = ttl_idle_seconds if ttl_idle_seconds is not None else self._config.ttl_idle_seconds

        # ------------------------------------------------------------------
        # Merged labels / annotations
        # ------------------------------------------------------------------
        extra_labels, extra_annotations = build_labels(
            default_labels=self._config.default_labels,
            call_labels=labels,
            thread_id=thread_id,
        )
        extra_annotations.update(
            build_ttl_annotations(ttl_seconds=eff_ttl, ttl_idle_seconds=eff_idle)
        )

        # ------------------------------------------------------------------
        # thread_id lookup
        # ------------------------------------------------------------------
        if thread_id is not None:
            existing = self._find_by_thread_id_internal(thread_id, eff_idle)
            if existing is not None:
                return existing

        # ------------------------------------------------------------------
        # sandbox_id reconnect (in-process cache or in-cluster)
        # ------------------------------------------------------------------
        if sandbox_id is not None:
            if sandbox_id in self._active_backends:
                logger.info("Reconnected to sandbox %s (in-process)", sandbox_id)
                return KubernetesSandbox(backend=self._active_backends[sandbox_id])
            # Not in cache → raise (consistent with original behaviour)
            raise SandboxNotFoundError(
                f"Sandbox '{sandbox_id}' is not active in this provider instance. "
                "It may have been deleted or created in a different process."
            )

        # ------------------------------------------------------------------
        # Warm-pool claim (raw mode with thread_id)
        # ------------------------------------------------------------------
        if (
            self._config.mode == "raw"
            and self._config.warm_pool_size > 0
            and thread_id is not None
        ):
            from langchain_kubernetes.backends.raw import RawK8sBackend

            backend = RawK8sBackend.claim_warm_pod(
                self._config,
                thread_id=thread_id,
                extra_labels=extra_labels,
                extra_annotations=extra_annotations,
                ttl_idle_seconds=eff_idle,
            )
            if backend is not None:
                self._active_backends[backend.id] = backend
                if thread_id is not None:
                    self._thread_id_map[thread_id] = backend.id
                logger.info("Claimed warm Pod %s for thread_id=%s", backend.id, thread_id)
                return KubernetesSandbox(backend=backend)

        # ------------------------------------------------------------------
        # Create new sandbox
        # ------------------------------------------------------------------
        backend = self._create_backend(
            extra_labels=extra_labels,
            extra_annotations=extra_annotations,
            ttl_idle_seconds=eff_idle,
        )
        self._active_backends[backend.id] = backend
        if thread_id is not None:
            self._thread_id_map[thread_id] = backend.id
        logger.info(
            "Created sandbox %s (mode=%s, namespace=%s)",
            backend.id,
            self._config.mode,
            self._config.namespace,
        )
        return KubernetesSandbox(backend=backend)

    def find_by_thread_id(self, thread_id: str) -> KubernetesSandbox | None:
        """Look up a sandbox by thread identifier without creating one.

        Queries the Kubernetes API with a label selector for the thread-id
        label. Works for both ``raw`` mode (Pods) and ``agent-sandbox`` mode
        (SandboxClaims).

        Args:
            thread_id: Thread / conversation identifier.

        Returns:
            :class:`~langchain_kubernetes.sandbox.KubernetesSandbox` if a
            usable sandbox is found, otherwise ``None``.
        """
        return self._find_by_thread_id_internal(thread_id, None)

    def list(
        self,
        cursor: str | None = None,
        *,
        labels: dict[str, str] | None = None,
        thread_id: str | None = None,
        status: str | None = None,
        **kwargs: Any,
    ) -> SandboxListResponse:
        """List sandboxes from the Kubernetes API with optional filtering.

        Unlike the previous in-process-only implementation, this method
        queries the cluster directly so it returns sandboxes created by any
        process.

        Args:
            cursor: Kubernetes ``continue`` token for pagination.
            labels: Filter by labels (keys auto-prefixed with our namespace
                prefix). ``None`` returns all managed sandboxes.
            thread_id: Syntactic sugar for filtering by thread-id label.
            status: Filter by status string: ``"running"``, ``"warm"``,
                ``"terminated"``.
            **kwargs: Unused.

        Returns:
            :class:`~langchain_kubernetes._types.SandboxListResponse`.
        """
        if self._config.mode == "raw":
            return self._list_raw(cursor=cursor, labels=labels, thread_id=thread_id, status=status)
        return self._list_agent_sandbox(cursor=cursor, labels=labels, thread_id=thread_id, status=status)

    def delete(self, *, sandbox_id: str, **kwargs: Any) -> None:
        """Delete a sandbox by cleaning up its backend resources.

        After deletion, if the raw warm pool is enabled, schedules
        background replenishment.

        Args:
            sandbox_id: Sandbox ID.
            **kwargs: Unused.
        """
        backend = self._active_backends.pop(sandbox_id, None)
        # Clean up thread_id map
        self._thread_id_map = {
            tid: sid for tid, sid in self._thread_id_map.items() if sid != sandbox_id
        }

        if backend is None:
            logger.debug("delete called for unknown sandbox %s — no-op", sandbox_id)
            return

        try:
            backend.cleanup()
            logger.info("Deleted sandbox %s", sandbox_id)
        except Exception as exc:
            logger.warning("Error while cleaning up sandbox %s: %s", sandbox_id, exc)

        # Replenish warm pool in the background
        if self._config.mode == "raw" and self._config.warm_pool_size > 0:
            self._schedule_replenish()

    def cleanup(self, max_idle_seconds: int | None = None) -> CleanupResult:
        """Delete sandboxes that have exceeded their TTL or idle threshold.

        Lists all managed sandboxes via the Kubernetes API, checks TTL and
        idle-time annotations, and deletes any that have expired.

        Args:
            max_idle_seconds: Override idle threshold for this call.  Sandboxes
                whose last-activity annotation is older than this many seconds
                are deleted.  Takes precedence over per-sandbox
                ``ttl-idle-seconds`` annotations if both are set.

        Returns:
            :class:`~langchain_kubernetes._types.CleanupResult` with deleted IDs
            and a count of kept sandboxes.
        """
        result = CleanupResult()
        now = datetime.now(timezone.utc)
        response = self.list()

        for info in response.sandboxes:
            should_delete = False

            ann = info.annotations

            # Check absolute TTL
            ttl_str = ann.get(ANN_TTL_SECONDS)
            created_str = ann.get(ANN_CREATED_AT)
            if ttl_str and created_str:
                try:
                    ttl = int(ttl_str)
                    created = datetime.fromisoformat(created_str)
                    if (now - created).total_seconds() > ttl:
                        should_delete = True
                        logger.info(
                            "Sandbox %s exceeded TTL (%ss)", info.id, ttl_str
                        )
                except (ValueError, TypeError):
                    pass

            # Check idle TTL
            idle_threshold = max_idle_seconds
            if idle_threshold is None:
                idle_str = ann.get(ANN_TTL_IDLE_SECONDS)
                if idle_str:
                    try:
                        idle_threshold = int(idle_str)
                    except (ValueError, TypeError):
                        pass

            if idle_threshold is not None:
                last_str = ann.get(ANN_LAST_ACTIVITY) or ann.get(ANN_CREATED_AT)
                if last_str:
                    try:
                        last = datetime.fromisoformat(last_str)
                        if (now - last).total_seconds() > idle_threshold:
                            should_delete = True
                            logger.info(
                                "Sandbox %s exceeded idle threshold (%ss)",
                                info.id,
                                idle_threshold,
                            )
                    except (ValueError, TypeError):
                        pass

            if should_delete:
                try:
                    if self._config.mode == "raw":
                        self._delete_raw_pod(info.id, info.namespace)
                    else:
                        self._delete_agent_sandbox_claim(info.id)
                    self._active_backends.pop(info.id, None)
                    result.deleted.append(info.id)
                except Exception as exc:
                    logger.warning("Failed to delete sandbox %s: %s", info.id, exc)
            else:
                result.kept += 1

        return result

    def stats(self, idle_threshold_seconds: int = 300) -> ProviderStats:
        """Return aggregate statistics for all managed sandboxes.

        Args:
            idle_threshold_seconds: Seconds since last execute() before a
                sandbox is considered idle. Default: 300.

        Returns:
            :class:`~langchain_kubernetes._types.ProviderStats`.
        """
        response = self.list()
        now = datetime.now(timezone.utc)
        running = warm = idle = 0
        thread_ids: set[str] = set()

        for info in response.sandboxes:
            if info.status == "running":
                running += 1
            elif info.status == "warm":
                warm += 1

            if info.thread_id:
                thread_ids.add(info.thread_id)

            last_str = info.annotations.get(ANN_LAST_ACTIVITY) or info.annotations.get(ANN_CREATED_AT)
            if last_str and info.status == "running":
                try:
                    last = datetime.fromisoformat(last_str)
                    if (now - last).total_seconds() > idle_threshold_seconds:
                        idle += 1
                except (ValueError, TypeError):
                    pass

        return ProviderStats(
            total=len(response.sandboxes),
            running=running,
            warm=warm,
            idle=idle,
            thread_ids=len(thread_ids),
        )

    def pool_status(self) -> WarmPoolStatus:
        """Return the current warm-pool status.

        For raw mode, counts Pods by their ``pool-status`` label.  For
        agent-sandbox mode, queries the SandboxWarmPool status if possible.

        Returns:
            :class:`~langchain_kubernetes._types.WarmPoolStatus`.
        """
        if self._config.mode != "raw":
            # agent-sandbox: basic count from list
            response = self.list()
            active = sum(1 for s in response.sandboxes if s.status == "running")
            available = 0  # warm pool managed by controller
            return WarmPoolStatus(
                available=available,
                active=active,
                total=active,
                target=0,
            )

        try:
            from langchain_kubernetes.backends.raw import RawK8sBackend
            core_v1, _ = RawK8sBackend.load_k8s_clients()
            warm_list = core_v1.list_namespaced_pod(
                namespace=self._config.namespace,
                label_selector=warm_pool_selector(),
            )
            available = len([
                p for p in (warm_list.items or [])
                if p.status and p.status.phase in ("Running", "Pending")
            ])
            active_list = core_v1.list_namespaced_pod(
                namespace=self._config.namespace,
                label_selector=f"{LABEL_POOL_STATUS}={POOL_STATUS_ACTIVE}",
            )
            active = len(active_list.items or [])
        except Exception as exc:
            logger.warning("Failed to query warm pool status: %s", exc)
            return WarmPoolStatus(available=0, active=0, total=0, target=self._config.warm_pool_size)

        return WarmPoolStatus(
            available=available,
            active=active,
            total=available + active,
            target=self._config.warm_pool_size,
        )

    # ------------------------------------------------------------------
    # SandboxProvider: async interface
    # ------------------------------------------------------------------

    async def aget_or_create(
        self,
        *,
        sandbox_id: str | None = None,
        thread_id: str | None = None,
        labels: dict[str, str] | None = None,
        ttl_seconds: int | None = None,
        ttl_idle_seconds: int | None = None,
        **kwargs: Any,
    ) -> KubernetesSandbox:
        """Async wrapper around :meth:`get_or_create`.

        Args:
            sandbox_id: Existing sandbox ID, or ``None`` to create new.
            thread_id: Thread identifier for label-based lookup.
            labels: Per-call labels.
            ttl_seconds: Absolute TTL override.
            ttl_idle_seconds: Idle TTL override.
            **kwargs: Forwarded to :meth:`get_or_create`.

        Returns:
            :class:`~langchain_kubernetes.sandbox.KubernetesSandbox`.
        """
        return await asyncio.to_thread(
            self.get_or_create,
            sandbox_id=sandbox_id,
            thread_id=thread_id,
            labels=labels,
            ttl_seconds=ttl_seconds,
            ttl_idle_seconds=ttl_idle_seconds,
            **kwargs,
        )

    async def alist(
        self,
        cursor: str | None = None,
        *,
        labels: dict[str, str] | None = None,
        thread_id: str | None = None,
        status: str | None = None,
        **kwargs: Any,
    ) -> SandboxListResponse:
        """Async wrapper around :meth:`list`.

        Args:
            cursor: Pagination cursor.
            labels: Label filter.
            thread_id: Thread-id filter.
            status: Status filter.
            **kwargs: Unused.

        Returns:
            :class:`~langchain_kubernetes._types.SandboxListResponse`.
        """
        return await asyncio.to_thread(
            self.list,
            cursor,
            labels=labels,
            thread_id=thread_id,
            status=status,
            **kwargs,
        )

    async def adelete(self, *, sandbox_id: str, **kwargs: Any) -> None:
        """Async wrapper around :meth:`delete`.

        Args:
            sandbox_id: Sandbox to delete.
            **kwargs: Unused.
        """
        await asyncio.to_thread(self.delete, sandbox_id=sandbox_id, **kwargs)

    async def acleanup(self, max_idle_seconds: int | None = None) -> CleanupResult:
        """Async wrapper around :meth:`cleanup`.

        Args:
            max_idle_seconds: Override idle threshold.

        Returns:
            :class:`~langchain_kubernetes._types.CleanupResult`.
        """
        return await asyncio.to_thread(self.cleanup, max_idle_seconds)

    async def astats(self, idle_threshold_seconds: int = 300) -> ProviderStats:
        """Async wrapper around :meth:`stats`.

        Args:
            idle_threshold_seconds: Idle threshold in seconds.

        Returns:
            :class:`~langchain_kubernetes._types.ProviderStats`.
        """
        return await asyncio.to_thread(self.stats, idle_threshold_seconds)

    # ------------------------------------------------------------------
    # Internal: backend factory
    # ------------------------------------------------------------------

    def _create_backend(
        self,
        extra_labels: dict[str, str] | None = None,
        extra_annotations: dict[str, str] | None = None,
        ttl_idle_seconds: int | None = None,
    ) -> KubernetesBackendProtocol:
        """Dispatch to the right backend factory based on ``config.mode``.

        Args:
            extra_labels: Additional labels for the sandbox resource.
            extra_annotations: Additional annotations for the sandbox resource.
            ttl_idle_seconds: Idle TTL to attach to the backend for activity tracking.

        Returns:
            Active backend ready to accept commands.
        """
        if self._config.mode == "agent-sandbox":
            return self._create_agent_sandbox_backend(
                extra_labels=extra_labels,
                extra_annotations=extra_annotations,
                ttl_idle_seconds=ttl_idle_seconds,
            )
        if self._config.mode == "raw":
            return self._create_raw_backend(
                extra_labels=extra_labels,
                extra_annotations=extra_annotations,
                ttl_idle_seconds=ttl_idle_seconds,
            )
        raise ValueError(f"Unknown mode: {self._config.mode!r}")

    def _create_agent_sandbox_backend(
        self,
        extra_labels: dict[str, str] | None = None,
        extra_annotations: dict[str, str] | None = None,
        ttl_idle_seconds: int | None = None,
    ) -> AgentSandboxBackend:
        """Build and enter a SandboxClient, return an AgentSandboxBackend."""
        client = _build_agent_sandbox_client(self._config)

        try:
            client.__enter__()
        except Exception as exc:
            _raise_clear_agent_sandbox_error(exc, self._config)

        sandbox_name: str = client.sandbox_name or client.claim_name or "unknown"
        claim_name: str = client.claim_name or sandbox_name

        logger.info(
            "agent-sandbox: provisioned %s (template=%s)",
            sandbox_name,
            self._config.template_name,
        )

        # Patch the SandboxClaim with our managed-by labels
        if extra_labels or extra_annotations:
            self._patch_sandbox_claim(claim_name, extra_labels or {}, extra_annotations or {})

        # Build activity callback if idle TTL is configured
        activity_callback = None
        if ttl_idle_seconds is not None:
            activity_callback = lambda: self._patch_claim_last_activity(claim_name)  # noqa: E731

        return AgentSandboxBackend(
            client=client,
            sandbox_name=sandbox_name,
            activity_callback=activity_callback,
        )

    def _create_raw_backend(
        self,
        extra_labels: dict[str, str] | None = None,
        extra_annotations: dict[str, str] | None = None,
        ttl_idle_seconds: int | None = None,
    ) -> "KubernetesBackendProtocol":
        """Provision a Pod and return a RawK8sBackend."""
        from langchain_kubernetes.backends.raw import RawK8sBackend

        return RawK8sBackend.create(
            self._config,
            extra_labels=extra_labels,
            extra_annotations=extra_annotations,
            ttl_idle_seconds=ttl_idle_seconds,
        )

    # ------------------------------------------------------------------
    # Internal: thread_id lookup
    # ------------------------------------------------------------------

    def _find_by_thread_id_internal(
        self,
        thread_id: str,
        ttl_idle_seconds: int | None,
    ) -> KubernetesSandbox | None:
        """Internal implementation for thread_id-based lookup.

        Args:
            thread_id: Thread identifier.
            ttl_idle_seconds: Idle TTL for activity tracking on reconnect.

        Returns:
            Sandbox if found, else None.
        """
        # Check in-process cache first
        if thread_id in self._thread_id_map:
            sandbox_id = self._thread_id_map[thread_id]
            if sandbox_id in self._active_backends:
                logger.debug("thread_id=%s → in-process cache hit", thread_id)
                return KubernetesSandbox(backend=self._active_backends[sandbox_id])

        if self._config.mode == "raw":
            return self._find_by_thread_id_raw(thread_id, ttl_idle_seconds)
        return self._find_by_thread_id_agent_sandbox(thread_id, ttl_idle_seconds)

    def _find_by_thread_id_raw(
        self,
        thread_id: str,
        ttl_idle_seconds: int | None,
    ) -> KubernetesSandbox | None:
        try:
            from langchain_kubernetes.backends.raw import RawK8sBackend

            backend = RawK8sBackend.find_by_thread_id(self._config, thread_id)
            if backend is not None:
                if ttl_idle_seconds is not None:
                    backend._ttl_idle_seconds = ttl_idle_seconds
                self._active_backends[backend.id] = backend
                self._thread_id_map[thread_id] = backend.id
                return KubernetesSandbox(backend=backend)
        except Exception as exc:
            logger.warning("thread_id lookup (raw) failed: %s", exc)
        return None

    def _find_by_thread_id_agent_sandbox(
        self,
        thread_id: str,
        ttl_idle_seconds: int | None,
    ) -> KubernetesSandbox | None:
        """Look up a SandboxClaim by thread-id label.

        Requires direct K8s API access (configured via ``kube_api_url`` or
        in-cluster service account).  When neither is available the lookup is
        skipped silently — deduplication falls back to the in-process cache
        managed by :class:`~langchain_kubernetes.manager.KubernetesSandboxManager`.
        """
        from langchain_kubernetes._k8s_http import is_k8s_api_configured

        if not is_k8s_api_configured(self._config.kube_api_url, self._config.kube_token):
            logger.debug(
                "thread_id lookup (agent-sandbox) skipped: no K8s API configured "
                "and not running in-cluster. Set kube_api_url for cross-process reconnection."
            )
            return None

        selector = thread_id_selector(thread_id)
        try:
            items = self._list_sandbox_claims(label_selector=selector)
        except Exception as exc:
            logger.warning("thread_id lookup (agent-sandbox) failed: %s", exc)
            return None

        for item in items:
            meta = item.get("metadata", {})
            name = meta.get("name", "")
            if not name:
                continue
            logger.info(
                "Found existing SandboxClaim %s for thread_id=%s", name, thread_id
            )
            client = _build_agent_sandbox_client(self._config)
            activity_callback = None
            if ttl_idle_seconds is not None:
                cn = name  # capture for closure
                activity_callback = lambda: self._patch_claim_last_activity(cn)  # noqa: E731
            backend = AgentSandboxBackend(
                client=client,
                sandbox_name=name,
                activity_callback=activity_callback,
            )
            self._active_backends[name] = backend
            self._thread_id_map[thread_id] = name
            return KubernetesSandbox(backend=backend)

        return None

    # ------------------------------------------------------------------
    # Internal: list operations
    # ------------------------------------------------------------------

    def _list_raw(
        self,
        cursor: str | None = None,
        labels: dict[str, str] | None = None,
        thread_id: str | None = None,
        status: str | None = None,
    ) -> SandboxListResponse:
        try:
            from langchain_kubernetes.backends.raw import RawK8sBackend
            core_v1, _ = RawK8sBackend.load_k8s_clients()
        except ImportError:
            return SandboxListResponse(sandboxes=[])

        selector = MANAGED_SELECTOR
        if labels:
            for k, v in labels.items():
                selector += f",{LABEL_PREFIX}{k}={v}"
        if thread_id:
            safe, _ = sanitize_label_value(thread_id)
            selector += f",{LABEL_THREAD_ID}={safe}"

        kwargs: dict[str, Any] = {
            "namespace": self._config.namespace,
            "label_selector": selector,
        }
        if cursor:
            kwargs["_continue"] = cursor

        try:
            pod_list = core_v1.list_namespaced_pod(**kwargs)
        except Exception as exc:
            logger.warning("Failed to list Pods: %s", exc)
            return SandboxListResponse(sandboxes=[])

        sandboxes = []
        for pod in (pod_list.items or []):
            info = _pod_to_sandbox_info(pod)
            if status and info.status != status:
                continue
            sandboxes.append(info)

        next_cursor = getattr(pod_list.metadata, "_continue", None) or None
        return SandboxListResponse(sandboxes=sandboxes, cursor=next_cursor)

    def _list_agent_sandbox(
        self,
        cursor: str | None = None,
        labels: dict[str, str] | None = None,
        thread_id: str | None = None,
        status: str | None = None,
    ) -> SandboxListResponse:
        selector = f"app.kubernetes.io/managed-by=deepagents"  # existing selector still works
        # Also add our managed-by selector
        from langchain_kubernetes._labels import LABEL_MANAGED_BY, LABEL_MANAGED_BY_VALUE
        # Build selector starting with our namespace prefix
        selector_parts = [f"{LABEL_MANAGED_BY}={LABEL_MANAGED_BY_VALUE}"]

        if labels:
            for k, v in labels.items():
                selector_parts.append(f"{LABEL_PREFIX}{k}={v}")
        if thread_id:
            safe, _ = sanitize_label_value(thread_id)
            selector_parts.append(f"{LABEL_THREAD_ID}={safe}")

        # Try without managed-by filter first to get all claims, then filter
        # For backward compat, list all claims and filter in-memory
        try:
            items = self._list_sandbox_claims(
                label_selector=",".join(selector_parts) if len(selector_parts) > 0 else None,
                continuation=cursor,
            )
        except Exception as exc:
            logger.warning("Failed to list SandboxClaims: %s", exc)
            # Fall back to in-process list
            sandboxes = [
                SandboxInfo(
                    id=sid,
                    namespace=self._config.namespace,
                    status="running",
                )
                for sid in self._active_backends
            ]
            return SandboxListResponse(sandboxes=sandboxes)

        sandboxes = []
        for item in items:
            info = _claim_to_sandbox_info(item, self._config.namespace)
            if status and info.status != status:
                continue
            sandboxes.append(info)

        return SandboxListResponse(sandboxes=sandboxes)

    def _list_sandbox_claims(
        self,
        label_selector: str | None = None,
        continuation: str | None = None,
    ) -> list[dict]:
        """List SandboxClaims from the k8s API."""
        from langchain_kubernetes._k8s_http import k8s_get

        path = (
            f"/apis/{_CLAIM_API_GROUP}/{_CLAIM_API_VERSION}"
            f"/namespaces/{self._config.namespace}/{_CLAIM_PLURAL}"
        )
        try:
            resp = k8s_get(
                api_url=self._config.kube_api_url,
                token_override=self._config.kube_token,
                path=path,
                label_selector=label_selector,
            )
            return resp.get("items", [])
        except Exception as exc:
            logger.debug("k8s_get SandboxClaims failed: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Internal: patch operations for agent-sandbox mode
    # ------------------------------------------------------------------

    def _patch_sandbox_claim(
        self,
        claim_name: str,
        labels: dict[str, str],
        annotations: dict[str, str],
    ) -> None:
        """Patch a SandboxClaim with labels and annotations.

        Best-effort: enables cross-process reconnection via ``find_by_thread_id``.
        Silently skipped when K8s API access is not configured (no ``kube_api_url``
        and not in-cluster). A warning is only emitted when K8s API IS configured
        but the request fails, since that indicates a fixable misconfiguration.
        """
        from langchain_kubernetes._k8s_http import is_k8s_api_configured, k8s_patch

        if not is_k8s_api_configured(self._config.kube_api_url, self._config.kube_token):
            logger.debug(
                "Skipping label patch for SandboxClaim %s: no K8s API configured "
                "and not in-cluster. Thread_id labels require kube_api_url.",
                claim_name,
            )
            return

        path = (
            f"/apis/{_CLAIM_API_GROUP}/{_CLAIM_API_VERSION}"
            f"/namespaces/{self._config.namespace}/{_CLAIM_PLURAL}/{claim_name}"
        )
        patch: dict[str, Any] = {"metadata": {}}
        if labels:
            patch["metadata"]["labels"] = labels
        if annotations:
            patch["metadata"]["annotations"] = annotations

        try:
            k8s_patch(
                api_url=self._config.kube_api_url,
                token_override=self._config.kube_token,
                path=path,
                patch=patch,
            )
        except Exception as exc:
            logger.warning(
                "Failed to patch SandboxClaim %s with labels: %s", claim_name, exc
            )

    def _patch_claim_last_activity(self, claim_name: str) -> None:
        """Update the last-activity annotation on a SandboxClaim.

        Best-effort fire-and-forget. Silently skipped when K8s API access is not
        configured (no ``kube_api_url`` and not in-cluster).
        """
        from langchain_kubernetes._k8s_http import is_k8s_api_configured, k8s_patch

        if not is_k8s_api_configured(self._config.kube_api_url, self._config.kube_token):
            return

        path = (
            f"/apis/{_CLAIM_API_GROUP}/{_CLAIM_API_VERSION}"
            f"/namespaces/{self._config.namespace}/{_CLAIM_PLURAL}/{claim_name}"
        )
        try:
            k8s_patch(
                api_url=self._config.kube_api_url,
                token_override=self._config.kube_token,
                path=path,
                patch={"metadata": {"annotations": {ANN_LAST_ACTIVITY: now_iso()}}},
            )
        except Exception as exc:
            logger.warning(
                "Failed to update last-activity on SandboxClaim %s: %s", claim_name, exc
            )

    def _delete_agent_sandbox_claim(self, claim_name: str) -> None:
        """Delete a SandboxClaim via the k8s API."""
        from langchain_kubernetes._k8s_http import k8s_get
        import urllib.request

        path = (
            f"/apis/{_CLAIM_API_GROUP}/{_CLAIM_API_VERSION}"
            f"/namespaces/{self._config.namespace}/{_CLAIM_PLURAL}/{claim_name}"
        )
        from langchain_kubernetes._k8s_http import (
            _IN_CLUSTER_API_URL,
            _build_ssl_context,
            _make_headers,
            _read_token,
        )

        base = (self._config.kube_api_url or _IN_CLUSTER_API_URL).rstrip("/")
        url = f"{base}{path}"
        token = _read_token(self._config.kube_token)
        headers = _make_headers(token)
        ctx = _build_ssl_context()

        import json

        req = urllib.request.Request(url, headers=headers, method="DELETE")
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=10):
                pass
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return
            raise

    def _delete_raw_pod(self, sandbox_id: str, namespace: str) -> None:
        """Delete a raw-mode Pod by sandbox_id."""
        try:
            from langchain_kubernetes.backends.raw import RawK8sBackend, _ApiException
            core_v1, networking_v1 = RawK8sBackend.load_k8s_clients()
            pod_name = f"deepagents-{sandbox_id}"
            try:
                core_v1.delete_namespaced_pod(name=pod_name, namespace=namespace)
            except _ApiException as exc:
                if exc.status != 404:
                    raise
        except ImportError:
            pass

    # ------------------------------------------------------------------
    # Internal: warm pool management
    # ------------------------------------------------------------------

    def _ensure_warm_pool(self) -> None:
        """Initialise the warm pool on first call (lazy, thread-safe)."""
        with self._warm_pool_lock:
            if self._warm_pool_initialised:
                return
            self._warm_pool_initialised = True

        self._replenish_warm_pool()

    def _replenish_warm_pool(self) -> None:
        """Create warm Pods until the pool reaches the target size."""
        if self._config.mode != "raw" or self._config.warm_pool_size <= 0:
            return

        try:
            from langchain_kubernetes.backends.raw import RawK8sBackend
            core_v1, _ = RawK8sBackend.load_k8s_clients()
        except ImportError:
            return

        try:
            warm_list = core_v1.list_namespaced_pod(
                namespace=self._config.namespace,
                label_selector=warm_pool_selector(),
            )
            current_count = len([
                p for p in (warm_list.items or [])
                if p.status and p.status.phase not in ("Failed", "Unknown", "Succeeded")
            ])
        except Exception as exc:
            logger.warning("Failed to count warm Pods: %s", exc)
            return

        needed = self._config.warm_pool_size - current_count
        if needed <= 0:
            return

        from langchain_kubernetes._labels import build_labels as _bl
        pool_labels, _ = _bl()
        pool_labels[LABEL_POOL_STATUS] = POOL_STATUS_WARM

        for _ in range(needed):
            try:
                from langchain_kubernetes.backends.raw import RawK8sBackend
                backend = RawK8sBackend.create(
                    self._config,
                    extra_labels=pool_labels,
                )
                logger.info("Created warm Pod %s", backend.id)
            except Exception as exc:
                logger.warning("Failed to create warm Pod: %s", exc)

    def _schedule_replenish(self) -> None:
        """Schedule warm pool replenishment in a background thread."""
        t = threading.Thread(target=self._replenish_warm_pool, daemon=True)
        t.start()


# ---------------------------------------------------------------------------
# agent-sandbox helpers
# ---------------------------------------------------------------------------


def _import_sandbox_client():
    """Import ``SandboxClient`` with a friendly error on missing package.

    Returns:
        The ``SandboxClient`` class.

    Raises:
        ImportError: When ``k8s-agent-sandbox`` is not installed.
    """
    try:
        from k8s_agent_sandbox import SandboxClient

        return SandboxClient
    except ImportError as exc:
        raise ImportError(
            "agent-sandbox mode requires the 'k8s-agent-sandbox' package. "
            "Install with: pip install langchain-kubernetes[agent-sandbox]"
        ) from exc


def _build_agent_sandbox_client(config: KubernetesProviderConfig):
    """Instantiate a ``SandboxClient`` from *config* (not yet entered).

    Args:
        config: Provider configuration.

    Returns:
        Un-entered ``SandboxClient`` ready to be used as a context manager.
    """
    SandboxClient = _import_sandbox_client()

    kwargs: dict[str, Any] = {
        "template_name": config.template_name,
        "namespace": config.namespace,
        "server_port": config.server_port,
        "sandbox_ready_timeout": config.startup_timeout_seconds,
    }

    if config.connection_mode == "gateway":
        if config.gateway_name is None:
            raise ValueError(
                "connection_mode='gateway' requires gateway_name to be set in KubernetesProviderConfig"
            )
        kwargs["gateway_name"] = config.gateway_name
        kwargs["gateway_namespace"] = config.gateway_namespace
    elif config.connection_mode == "direct":
        if config.api_url is None:
            raise ValueError(
                "connection_mode='direct' requires api_url to be set in KubernetesProviderConfig"
            )
        kwargs["api_url"] = config.api_url
    # "tunnel" mode: no extra kwargs

    # Warm pool reference
    if config.warm_pool_name:
        kwargs["warm_pool_name"] = config.warm_pool_name

    return SandboxClient(**kwargs)


def _raise_clear_agent_sandbox_error(
    exc: Exception, config: KubernetesProviderConfig
) -> None:
    """Re-raise *exc* with a human-readable message for common failure modes."""
    msg = str(exc).lower()

    # Guard: re-raise dev-server enforcement errors (e.g. blockbuster.BlockingError,
    # which says "Blocking call to socket.socket.connect") without wrapping them.
    # These errors contain keywords like "connect" that would otherwise trigger the
    # connectivity hint below, completely hiding the real cause from the developer.
    if "blocking call" in msg:
        raise exc

    if "timeout" in msg or isinstance(exc, TimeoutError):
        raise TimeoutError(
            f"Sandbox did not become ready within {config.startup_timeout_seconds}s. "
            "Check controller logs: kubectl logs -n agent-sandbox-system "
            "-l app=agent-sandbox-controller"
        ) from exc

    if "not found" in msg and config.template_name and config.template_name in str(exc):
        raise RuntimeError(
            f"SandboxTemplate '{config.template_name}' not found in namespace "
            f"'{config.namespace}'. Create it first — see README."
        ) from exc

    if any(kw in msg for kw in ("connection refused", "unreachable", "connect", "router")):
        mode_hint = {
            "tunnel": "Ensure kubectl is available and the sandbox-router Service exists.",
            "gateway": f"Ensure the Gateway '{config.gateway_name}' is deployed and reachable.",
            "direct": f"Ensure the sandbox-router is accessible at '{config.api_url}'.",
        }.get(config.connection_mode, "")
        raise RuntimeError(
            f"Cannot reach the sandbox-router (connection_mode='{config.connection_mode}'). "
            f"{mode_hint}"
        ) from exc

    raise exc


# ---------------------------------------------------------------------------
# Info helpers
# ---------------------------------------------------------------------------


def _pod_to_sandbox_info(pod: Any) -> SandboxInfo:
    """Convert a k8s Pod object to SandboxInfo."""
    from langchain_kubernetes.backends.raw_manifests import LABEL_SANDBOX_ID

    meta = pod.metadata or object()
    name = getattr(meta, "name", "unknown") or "unknown"
    namespace = getattr(meta, "namespace", "default") or "default"
    labels: dict[str, str] = dict(getattr(meta, "labels", {}) or {})
    annotations: dict[str, str] = dict(getattr(meta, "annotations", {}) or {})

    sandbox_id = labels.get(LABEL_SANDBOX_ID, name.removeprefix("deepagents-"))
    thread_id_val = labels.get(LABEL_THREAD_ID)
    pool_status = labels.get(LABEL_POOL_STATUS)

    phase = None
    if pod.status:
        phase = pod.status.phase

    if pool_status == POOL_STATUS_WARM:
        status = "warm"
    elif phase == "Running":
        status = "running"
    elif phase in ("Succeeded", "Failed"):
        status = "terminated"
    else:
        status = phase.lower() if phase else "pending"

    return SandboxInfo(
        id=sandbox_id,
        namespace=namespace,
        thread_id=thread_id_val,
        labels=labels,
        annotations=annotations,
        created_at=annotations.get(ANN_CREATED_AT),
        last_activity=annotations.get(ANN_LAST_ACTIVITY),
        status=status,
    )


def _claim_to_sandbox_info(item: dict, default_namespace: str) -> SandboxInfo:
    """Convert a raw SandboxClaim dict to SandboxInfo."""
    meta = item.get("metadata", {})
    name = meta.get("name", "unknown")
    namespace = meta.get("namespace", default_namespace)
    labels: dict[str, str] = meta.get("labels") or {}
    annotations: dict[str, str] = meta.get("annotations") or {}

    thread_id_val = labels.get(LABEL_THREAD_ID)

    # Determine status from claim/sandbox conditions
    status_obj = item.get("status", {})
    conditions = status_obj.get("conditions", []) if isinstance(status_obj, dict) else []
    ready = any(
        c.get("type") == "Ready" and c.get("status") == "True"
        for c in conditions
        if isinstance(c, dict)
    )
    status = "running" if ready else "pending"

    return SandboxInfo(
        id=name,
        namespace=namespace,
        thread_id=thread_id_val,
        labels=labels,
        annotations=annotations,
        created_at=annotations.get(ANN_CREATED_AT),
        last_activity=annotations.get(ANN_LAST_ACTIVITY),
        status=status,
    )
