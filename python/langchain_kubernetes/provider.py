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
from langchain_kubernetes.config import KubernetesProviderConfig
from langchain_kubernetes.sandbox import KubernetesSandbox

logger = logging.getLogger(__name__)


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
            "k8s-agent-sandbox package not installed. "
            "Run: pip install k8s-agent-sandbox"
        ) from exc


def _build_client(config: KubernetesProviderConfig):
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
    # "tunnel" mode: no extra kwargs — SandboxClient opens a kubectl port-forward automatically

    return SandboxClient(**kwargs)


class KubernetesProvider(SandboxProvider):
    """Lifecycle manager for Kubernetes-based sandboxes via agent-sandbox.

    Each sandbox is a ``Sandbox`` CR managed by the ``kubernetes-sigs/agent-sandbox``
    controller. This provider does **not** manage Pods, Namespaces, or NetworkPolicies
    directly — all of that is handled by the controller and the ``SandboxTemplate`` CRD.

    **Prerequisites** (must be installed in the cluster before use):

    - ``kubernetes-sigs/agent-sandbox`` controller and CRDs
    - sandbox-router Deployment
    - At least one ``SandboxTemplate`` CR matching ``config.template_name``

    See the README for installation instructions.

    Active sandbox clients are tracked in-process. Calling :meth:`delete` exits the
    underlying ``SandboxClient`` context which triggers Sandbox CR cleanup. Sandboxes
    created in a previous process are not visible to :meth:`list`.

    Args:
        config: Provider configuration. ``template_name`` is required.
    """

    def __init__(self, config: KubernetesProviderConfig) -> None:
        self._config = config
        # Maps sandbox_name -> entered SandboxClient
        self._active_clients: dict[str, Any] = {}

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

        When *sandbox_id* is provided and matches an active sandbox managed by
        this provider instance, the existing :class:`~langchain_kubernetes.sandbox.KubernetesSandbox`
        is returned. Otherwise a new ``SandboxClient`` context is entered,
        provisioning a fresh Sandbox CR (potentially claimed from a warm pool).

        Args:
            sandbox_id: Existing sandbox name to reconnect to (must be active in
                this provider instance). Pass ``None`` to always create a new sandbox.
            **kwargs: Unused; present for interface compatibility.

        Returns:
            :class:`~langchain_kubernetes.sandbox.KubernetesSandbox` instance.

        Raises:
            SandboxNotFoundError: When *sandbox_id* is given but not active.
            ImportError: When ``k8s-agent-sandbox`` is not installed.
            TimeoutError: If the sandbox does not become ready within
                ``startup_timeout_seconds``.
            RuntimeError: On sandbox creation failure.
        """
        if sandbox_id is not None:
            if sandbox_id not in self._active_clients:
                raise SandboxNotFoundError(
                    f"Sandbox '{sandbox_id}' is not active in this provider. "
                    "It may have been deleted or created in a different process."
                )
            client = self._active_clients[sandbox_id]
            logger.info("Reconnected to sandbox %s", sandbox_id)
            return KubernetesSandbox(client=client, sandbox_name=sandbox_id)

        # Create new sandbox
        client = _build_client(self._config)

        try:
            client.__enter__()
        except Exception as exc:
            _raise_clear_error(exc, self._config)

        sandbox_name: str = client.sandbox_name or client.claim_name or "unknown"
        self._active_clients[sandbox_name] = client
        logger.info(
            "Created sandbox %s (template=%s, namespace=%s)",
            sandbox_name,
            self._config.template_name,
            self._config.namespace,
        )
        return KubernetesSandbox(client=client, sandbox_name=sandbox_name)

    def list(self, cursor: str | None = None, **kwargs: Any) -> list[SandboxBackendProtocol]:
        """List all active sandboxes managed by this provider instance.

        Note: Only sandboxes created through *this* provider instance are visible.
        Sandboxes from other processes or provider instances are not listed.

        Args:
            cursor: Unused; present for interface compatibility.
            **kwargs: Unused.

        Returns:
            List of :class:`~langchain_kubernetes.sandbox.KubernetesSandbox` instances.
        """
        return [
            KubernetesSandbox(client=client, sandbox_name=name)
            for name, client in self._active_clients.items()
        ]

    def delete(self, *, sandbox_id: str, **kwargs: Any) -> None:
        """Delete a sandbox by exiting its ``SandboxClient`` context.

        Idempotent: deleting an unknown or already-deleted sandbox is a no-op.

        Args:
            sandbox_id: Sandbox name (as returned by
                :attr:`~langchain_kubernetes.sandbox.KubernetesSandbox.id`).
            **kwargs: Unused.
        """
        client = self._active_clients.pop(sandbox_id, None)
        if client is None:
            logger.debug("delete called for unknown sandbox %s — no-op", sandbox_id)
            return

        try:
            client.__exit__(None, None, None)
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
            sandbox_id: Existing sandbox name, or ``None`` to create new.
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


def _raise_clear_error(exc: Exception, config: KubernetesProviderConfig) -> None:
    """Re-raise *exc* with a human-readable message based on common failure modes.

    Args:
        exc: Original exception from ``SandboxClient.__enter__()``.
        config: Provider config for context in the error message.

    Raises:
        TimeoutError: When the sandbox did not become ready in time.
        RuntimeError: For template-not-found and router-not-reachable errors.
    """
    msg = str(exc).lower()

    if "timeout" in msg or isinstance(exc, TimeoutError):
        raise TimeoutError(
            f"Sandbox did not become ready within {config.startup_timeout_seconds}s. "
            "Check controller logs: kubectl logs -n agent-sandbox-system -l app=agent-sandbox-controller"
        ) from exc

    if "not found" in msg and config.template_name in str(exc):
        raise RuntimeError(
            f"SandboxTemplate '{config.template_name}' not found in namespace '{config.namespace}'. "
            "Create it first — see README."
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
