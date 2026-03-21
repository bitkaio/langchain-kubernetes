"""KubernetesSandboxManager: LangGraph-integrated per-thread sandbox manager."""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from typing import TYPE_CHECKING, Any, Callable

from langchain_kubernetes.config import KubernetesProviderConfig
from langchain_kubernetes.provider import KubernetesProvider
from langchain_kubernetes.sandbox import KubernetesSandbox

if TYPE_CHECKING:
    pass  # Avoid circular imports; RunnableConfig is imported lazily

logger = logging.getLogger(__name__)


class KubernetesSandboxManager:
    """High-level manager that integrates :class:`~langchain_kubernetes.provider.KubernetesProvider`
    with LangGraph's thread-based execution model.

    Provides a :attr:`backend_factory` callable suitable for passing to
    LangGraph's ``create_deep_agent(backend=...)``.  The factory extracts
    ``thread_id`` from the LangGraph ``RunnableConfig`` and calls
    :meth:`~KubernetesProvider.get_or_create` with the configured TTL settings,
    caching sandbox instances so that repeated calls for the same ``thread_id``
    return the same :class:`~langchain_kubernetes.sandbox.KubernetesSandbox`.

    Example::

        from langchain_kubernetes import KubernetesProviderConfig
        from langchain_kubernetes.manager import KubernetesSandboxManager

        manager = KubernetesSandboxManager(
            KubernetesProviderConfig(
                mode="agent-sandbox",
                template_name="python-sandbox-template",
            ),
            ttl_seconds=3600,
            ttl_idle_seconds=600,
        )

        # Use with LangGraph create_deep_agent
        agent = create_deep_agent(
            model=model,
            backend=manager.backend_factory,
        )

        # Or use as a context manager
        with manager:
            agent = create_deep_agent(model=model, backend=manager.backend_factory)

    Args:
        provider_config: Configuration for the underlying
            :class:`~langchain_kubernetes.provider.KubernetesProvider`.
        ttl_seconds: Absolute TTL from creation, passed to every
            :meth:`~KubernetesProvider.get_or_create` call.
        ttl_idle_seconds: Idle TTL from last execute(), passed to every
            :meth:`~KubernetesProvider.get_or_create` call.
        default_labels: Labels applied to every provisioned sandbox.
    """

    def __init__(
        self,
        provider_config: KubernetesProviderConfig,
        ttl_seconds: int | None = None,
        ttl_idle_seconds: int | None = None,
        default_labels: dict[str, str] | None = None,
    ) -> None:
        self._provider = KubernetesProvider(provider_config)
        self._ttl_seconds = ttl_seconds
        self._ttl_idle_seconds = ttl_idle_seconds
        self._default_labels = default_labels
        # thread_id -> KubernetesSandbox
        self._cache: dict[str, KubernetesSandbox] = {}
        # Guards mutations to _cache and _thread_locks
        self._lock = threading.Lock()
        # Per-thread-id locks serialise concurrent provisioning for the same thread.
        # Without these, N concurrent callers that all miss the cache simultaneously
        # would each call get_or_create and produce N orphaned sandboxes.
        self._thread_locks: dict[str, threading.Lock] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def backend_factory(self) -> Callable[[Any], KubernetesSandbox]:
        """Return a callable that provisions a per-thread sandbox.

        The returned callable accepts a LangGraph ``RunnableConfig`` dict and
        returns the :class:`~langchain_kubernetes.sandbox.KubernetesSandbox` for
        that thread.  Repeated calls with the same ``thread_id`` return the
        same cached sandbox.

        If ``thread_id`` is missing from the config, a UUID is generated and a
        warning is logged.

        Returns:
            A sync callable ``(config: RunnableConfig) -> KubernetesSandbox``.
        """

        def _factory(config: Any) -> KubernetesSandbox:
            thread_id = _extract_thread_id(config)
            return self._get_or_create_cached(thread_id)

        return _factory

    async def abackend_factory(self, config: Any) -> KubernetesSandbox:
        """Async variant of :attr:`backend_factory`.

        Runs the blocking ``get_or_create`` call in the default thread-pool
        executor so the event loop is never blocked.

        Args:
            config: LangGraph ``RunnableConfig`` dict.

        Returns:
            :class:`~langchain_kubernetes.sandbox.KubernetesSandbox` for the thread.
        """
        thread_id = _extract_thread_id(config)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_or_create_cached, thread_id)

    def get_sandbox(self, thread_id: str) -> KubernetesSandbox | None:
        """Get a cached sandbox by thread_id without creating a new one.

        Args:
            thread_id: Thread / conversation identifier.

        Returns:
            The cached :class:`~langchain_kubernetes.sandbox.KubernetesSandbox`, or
            ``None`` if no sandbox exists for this thread.
        """
        with self._lock:
            return self._cache.get(thread_id)

    def shutdown(self) -> None:
        """Delete all tracked sandboxes and clear the internal cache.

        Calls :meth:`~KubernetesProvider.delete` for each cached sandbox.
        Errors during deletion are logged but not re-raised.
        """
        with self._lock:
            sandboxes = list(self._cache.items())
            self._cache.clear()
            self._thread_locks.clear()

        for thread_id, sandbox in sandboxes:
            try:
                self._provider.delete(sandbox_id=sandbox.id)
                logger.info("Shutdown: deleted sandbox %s (thread_id=%s)", sandbox.id, thread_id)
            except Exception as exc:
                logger.warning(
                    "Shutdown: failed to delete sandbox %s: %s", sandbox.id, exc
                )

    async def ashutdown(self) -> None:
        """Async variant of :meth:`shutdown`."""
        await asyncio.to_thread(self.shutdown)

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "KubernetesSandboxManager":
        return self

    def __exit__(self, *args: Any) -> None:
        self.shutdown()

    async def __aenter__(self) -> "KubernetesSandboxManager":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.ashutdown()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_or_create_cached(self, thread_id: str) -> KubernetesSandbox:
        """Get from cache or call provider.get_or_create with TTL settings.

        Cache hits are returned immediately with no I/O, safe from any context.

        Cache misses acquire a per-thread-id lock before provisioning.  This
        serialises concurrent callers for the *same* ``thread_id`` so that
        only the first caller provisions a sandbox; the rest find it in the
        cache when they re-check inside the per-thread lock.  Without this,
        N concurrent async workers (as used by the LangGraph Platform server)
        would each race to call ``get_or_create`` and create N orphaned
        sandboxes for the same thread.

        Cache misses also refuse to run if called from inside a running asyncio
        event loop — use :meth:`abackend_factory` in async contexts instead,
        which wraps this call in ``loop.run_in_executor``.
        """
        # ── Fast path: cache hit, no I/O ─────────────────────────────────────
        with self._lock:
            if thread_id in self._cache:
                return self._cache[thread_id]

        # ── Cache miss: refuse to block the event loop ────────────────────────
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass  # no running loop — safe to proceed synchronously
        else:
            raise RuntimeError(
                "KubernetesSandboxManager.backend_factory was called with a cache miss "
                "from inside a running asyncio event loop. Blocking socket I/O on the "
                "event loop degrades ASGI servers and is caught by blockbuster in dev mode.\n\n"
                "Use one of these alternatives:\n"
                "  1. await manager.abackend_factory(config)  — wraps blocking I/O in a thread\n"
                "  2. await asyncio.to_thread(manager._get_or_create_cached, thread_id)  — pre-warm\n"
                "  3. Call manager._get_or_create_cached(thread_id) synchronously before "
                "starting the event loop (e.g. in lifespan startup)."
            )

        # ── Acquire per-thread lock to serialise concurrent provisioning ──────
        # Multiple workers may reach here simultaneously for the same thread_id.
        # Only one should call get_or_create; the rest must wait and then reuse
        # the sandbox the winner stored in the cache.
        with self._lock:
            if thread_id not in self._thread_locks:
                self._thread_locks[thread_id] = threading.Lock()
            thread_lock = self._thread_locks[thread_id]

        with thread_lock:
            # Re-check: a concurrent caller may have provisioned while we waited
            with self._lock:
                if thread_id in self._cache:
                    return self._cache[thread_id]

            sandbox = self._provider.get_or_create(
                thread_id=thread_id,
                labels=self._default_labels,
                ttl_seconds=self._ttl_seconds,
                ttl_idle_seconds=self._ttl_idle_seconds,
            )

            with self._lock:
                self._cache[thread_id] = sandbox

        return self._cache[thread_id]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_thread_id(config: Any) -> str:
    """Extract ``thread_id`` from a LangGraph RunnableConfig.

    Args:
        config: Dict-like RunnableConfig, or any object with a
            ``configurable`` attribute.

    Returns:
        Thread identifier string.  Falls back to a generated UUID if the
        config does not contain a thread_id.
    """
    thread_id: str | None = None

    if isinstance(config, dict):
        configurable = config.get("configurable", {})
        if isinstance(configurable, dict):
            thread_id = configurable.get("thread_id")
    else:
        configurable = getattr(config, "configurable", None)
        if isinstance(configurable, dict):
            thread_id = configurable.get("thread_id")

    if not thread_id:
        thread_id = str(uuid.uuid4())
        logger.warning(
            "thread_id not found in RunnableConfig — generated UUID %s. "
            "Pass configurable={'thread_id': '...'} to your LangGraph invocation.",
            thread_id,
        )

    return thread_id
