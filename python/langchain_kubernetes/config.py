"""Configuration dataclass for KubernetesProvider."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class KubernetesProviderConfig:
    """Configuration for :class:`~langchain_kubernetes.provider.KubernetesProvider`.

    Pod-level configuration (image, resources, securityContext, runtimeClassName,
    network policies) is defined in ``SandboxTemplate`` CRDs — not here. This config
    only contains connection and template-selection parameters.

    Attributes:
        template_name: Name of the ``SandboxTemplate`` CR to use. Must already exist
            in the cluster. Required.
        namespace: Kubernetes namespace where sandboxes are created.
        connection_mode: How to connect to the sandbox-router.

            - ``"tunnel"`` — auto port-forward via kubectl (default, good for local dev).
            - ``"gateway"`` — route through a Kubernetes Gateway resource.
            - ``"direct"`` — connect to an explicit URL (for in-cluster or custom domains).
        gateway_name: Name of the Gateway resource. Only used when
            ``connection_mode="gateway"``.
        gateway_namespace: Namespace of the Gateway resource. Defaults to ``namespace``.
        api_url: Full URL of the sandbox-router. Only used when
            ``connection_mode="direct"``.
        server_port: Port that the sandbox runtime listens on.
        startup_timeout_seconds: Seconds to wait for a sandbox to become ready before
            raising a :class:`TimeoutError`.
        default_exec_timeout: Default timeout (seconds) for individual ``execute()``
            calls when no per-call timeout is given.
    """

    template_name: str
    namespace: str = "default"
    connection_mode: str = "tunnel"
    gateway_name: str | None = None
    gateway_namespace: str = "default"
    api_url: str | None = None
    server_port: int = 8888
    startup_timeout_seconds: int = 120
    default_exec_timeout: int = 60 * 30  # 30 minutes
