"""Configuration dataclass for KubernetesProvider."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class KubernetesProviderConfig:
    """Configuration for :class:`~langchain_kubernetes.provider.KubernetesProvider`.

    Attributes:
        namespace: Default namespace where sandbox Pods are created when
            ``namespace_per_sandbox`` is ``False``.
        namespace_per_sandbox: When ``True``, each sandbox is isolated in its
            own Kubernetes namespace.  The sandbox ID becomes
            ``"{namespace}/{pod-name}"``.  When ``False`` (default), all Pods
            share ``namespace``.
        image: Container image for the sandbox Pod.
        image_pull_policy: Kubernetes ``imagePullPolicy`` for the sandbox
            container.  Valid values: ``"Always"``, ``"IfNotPresent"``,
            ``"Never"``.
        container_name: Name of the container inside the Pod.
        block_network: When ``True`` (default), attach a deny-all
            ``NetworkPolicy`` to every sandbox Pod.
        cpu_request: Kubernetes CPU resource *request* for the sandbox
            container (e.g. ``"100m"``).
        cpu_limit: Kubernetes CPU resource *limit* (e.g. ``"2"``).
        memory_request: Kubernetes memory resource *request* (e.g. ``"128Mi"``).
        memory_limit: Kubernetes memory resource *limit* (e.g. ``"512Mi"``).
        startup_timeout: Seconds to wait for the Pod to become ``Running``
            before raising and cleaning up.
        default_exec_timeout: Default timeout in seconds for individual
            ``execute()`` calls when no per-call timeout is supplied.
            ``None`` means wait indefinitely.
        kubeconfig: Path to the kubeconfig file.  ``None`` uses the in-cluster
            service-account credentials or the default ``~/.kube/config``.
        context: Kubernetes context name to use from the kubeconfig.  ``None``
            uses the active context.
    """

    namespace: str = "deepagents-sandboxes"
    namespace_per_sandbox: bool = False
    image: str = "python:3.12-slim"
    image_pull_policy: str = "IfNotPresent"
    container_name: str = "sandbox"
    block_network: bool = True
    cpu_request: str | None = "100m"
    cpu_limit: str | None = "2"
    memory_request: str | None = "128Mi"
    memory_limit: str | None = "512Mi"
    startup_timeout: float = 120.0
    default_exec_timeout: int | None = 30 * 60  # 30 minutes
    kubeconfig: str | None = None
    context: str | None = None
    extra_env: dict[str, str] = field(default_factory=dict)
