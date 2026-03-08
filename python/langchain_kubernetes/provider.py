"""KubernetesProvider: manages the lifecycle of KubernetesSandbox instances."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from deepagents.backends.protocol import SandboxBackendProtocol

from langchain_kubernetes._provider_base import (
    SandboxNotFoundError,
    SandboxProvider,
)
from langchain_kubernetes.backends.agent_sandbox import AgentSandboxBackend
from langchain_kubernetes.backends.protocol import KubernetesBackendProtocol
from langchain_kubernetes.config import KubernetesProviderConfig
from langchain_kubernetes.sandbox import KubernetesSandbox

logger = logging.getLogger(__name__)


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
    previous process are not visible to :meth:`list`.

    Args:
        config: Provider configuration. For agent-sandbox mode, ``template_name``
            is required. For raw mode, image and resource fields are used.
    """

    def __init__(self, config: KubernetesProviderConfig) -> None:
        self._config = config
        # Maps sandbox_id -> active backend
        self._active_backends: dict[str, KubernetesBackendProtocol] = {}

    # ------------------------------------------------------------------
    # SandboxProvider: sync interface
    # ------------------------------------------------------------------

    def get_or_create(
        self,
        *,
        sandbox_id: str | None = None,
        **kwargs: Any,
    ) -> SandboxBackendProtocol:
        """Return an existing sandbox or create a new one.

        When *sandbox_id* is provided and the sandbox is active in this
        provider instance, the existing
        :class:`~langchain_kubernetes.sandbox.KubernetesSandbox` is returned.
        Otherwise a new sandbox is provisioned according to ``config.mode``.

        Args:
            sandbox_id: Existing sandbox ID to reconnect to (must be active in
                this provider instance). Pass ``None`` to always create new.
            **kwargs: Unused; present for interface compatibility.

        Returns:
            :class:`~langchain_kubernetes.sandbox.KubernetesSandbox` instance.

        Raises:
            SandboxNotFoundError: When *sandbox_id* is given but not active.
            ImportError: When the required backend package is not installed.
            TimeoutError: If the sandbox does not become ready in time.
            RuntimeError: On sandbox creation failure.
            ValueError: If ``config.mode`` is unrecognised.
        """
        if sandbox_id is not None:
            if sandbox_id not in self._active_backends:
                raise SandboxNotFoundError(
                    f"Sandbox '{sandbox_id}' is not active in this provider instance. "
                    "It may have been deleted or created in a different process."
                )
            logger.info("Reconnected to sandbox %s", sandbox_id)
            return KubernetesSandbox(backend=self._active_backends[sandbox_id])

        backend = self._create_backend()
        self._active_backends[backend.id] = backend
        logger.info(
            "Created sandbox %s (mode=%s, namespace=%s)",
            backend.id,
            self._config.mode,
            self._config.namespace,
        )
        return KubernetesSandbox(backend=backend)

    def list(self, cursor: str | None = None, **kwargs: Any) -> list[SandboxBackendProtocol]:
        """List all active sandboxes managed by this provider instance.

        Only sandboxes created through *this* provider instance are visible.

        Args:
            cursor: Unused; present for interface compatibility.
            **kwargs: Unused.

        Returns:
            List of :class:`~langchain_kubernetes.sandbox.KubernetesSandbox` instances.
        """
        return [
            KubernetesSandbox(backend=backend)
            for backend in self._active_backends.values()
        ]

    def delete(self, *, sandbox_id: str, **kwargs: Any) -> None:
        """Delete a sandbox by cleaning up its backend resources.

        Idempotent: deleting an unknown or already-deleted sandbox is a no-op.

        Args:
            sandbox_id: Sandbox ID (as returned by
                :attr:`~langchain_kubernetes.sandbox.KubernetesSandbox.id`).
            **kwargs: Unused.
        """
        backend = self._active_backends.pop(sandbox_id, None)
        if backend is None:
            logger.debug("delete called for unknown sandbox %s — no-op", sandbox_id)
            return

        try:
            backend.cleanup()
            logger.info("Deleted sandbox %s", sandbox_id)
        except Exception as exc:
            logger.warning("Error while cleaning up sandbox %s: %s", sandbox_id, exc)

    # ------------------------------------------------------------------
    # SandboxProvider: async interface
    # ------------------------------------------------------------------

    async def aget_or_create(
        self,
        *,
        sandbox_id: str | None = None,
        **kwargs: Any,
    ) -> SandboxBackendProtocol:
        """Async wrapper around :meth:`get_or_create`.

        Args:
            sandbox_id: Existing sandbox ID, or ``None`` to create new.
            **kwargs: Forwarded to :meth:`get_or_create`.

        Returns:
            :class:`~langchain_kubernetes.sandbox.KubernetesSandbox`.
        """
        return await asyncio.to_thread(self.get_or_create, sandbox_id=sandbox_id, **kwargs)

    async def alist(self, cursor: str | None = None, **kwargs: Any) -> list[SandboxBackendProtocol]:
        """Async wrapper around :meth:`list`.

        Args:
            cursor: Unused.
            **kwargs: Unused.

        Returns:
            List of active sandboxes.
        """
        return self.list(cursor=cursor, **kwargs)

    async def adelete(self, *, sandbox_id: str, **kwargs: Any) -> None:
        """Async wrapper around :meth:`delete`.

        Args:
            sandbox_id: Sandbox to delete.
            **kwargs: Unused.
        """
        await asyncio.to_thread(self.delete, sandbox_id=sandbox_id, **kwargs)

    # ------------------------------------------------------------------
    # Internal: backend factory
    # ------------------------------------------------------------------

    def _create_backend(self) -> KubernetesBackendProtocol:
        """Dispatch to the right backend factory based on ``config.mode``.

        Returns:
            Active backend ready to accept commands.

        Raises:
            ValueError: If mode is unrecognised (should be caught by config validation).
        """
        if self._config.mode == "agent-sandbox":
            return self._create_agent_sandbox_backend()
        if self._config.mode == "raw":
            return self._create_raw_backend()
        raise ValueError(f"Unknown mode: {self._config.mode!r}")

    def _create_agent_sandbox_backend(self) -> AgentSandboxBackend:
        """Build and enter a SandboxClient, return an AgentSandboxBackend.

        Returns:
            Active :class:`~langchain_kubernetes.backends.agent_sandbox.AgentSandboxBackend`.

        Raises:
            ImportError: When ``k8s-agent-sandbox`` is not installed.
            TimeoutError, RuntimeError: On provisioning failure.
        """
        client = _build_agent_sandbox_client(self._config)

        try:
            client.__enter__()
        except Exception as exc:
            _raise_clear_agent_sandbox_error(exc, self._config)

        sandbox_name: str = client.sandbox_name or client.claim_name or "unknown"
        logger.info(
            "agent-sandbox: provisioned %s (template=%s)",
            sandbox_name,
            self._config.template_name,
        )
        return AgentSandboxBackend(client=client, sandbox_name=sandbox_name)

    def _create_raw_backend(self) -> "KubernetesBackendProtocol":
        """Provision a Pod and return a RawK8sBackend.

        Returns:
            Active :class:`~langchain_kubernetes.backends.raw.RawK8sBackend`.
        """
        from langchain_kubernetes.backends.raw import RawK8sBackend

        return RawK8sBackend.create(self._config)


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

    Raises:
        ValueError: For missing required connection params.
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

    return SandboxClient(**kwargs)


def _raise_clear_agent_sandbox_error(
    exc: Exception, config: KubernetesProviderConfig
) -> None:
    """Re-raise *exc* with a human-readable message for common failure modes.

    Args:
        exc: Original exception from ``SandboxClient.__enter__()``.
        config: Provider config for context in the error message.

    Raises:
        TimeoutError, RuntimeError: Always — never returns normally.
    """
    msg = str(exc).lower()

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
