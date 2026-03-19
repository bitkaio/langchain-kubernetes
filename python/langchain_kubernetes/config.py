"""Configuration dataclass for KubernetesProvider."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class KubernetesProviderConfig:
    """Configuration for :class:`~langchain_kubernetes.provider.KubernetesProvider`.

    Two backend modes are supported:

    - ``"agent-sandbox"`` (default) — uses the ``kubernetes-sigs/agent-sandbox``
      controller and ``k8s-agent-sandbox`` SDK. Requires CRDs and the controller
      to be installed in the cluster. Pod-level config (image, resources, security)
      lives in ``SandboxTemplate`` CRDs, **not** here.
    - ``"raw"`` — directly manages ephemeral Pods via the Kubernetes API. Works on
      any cluster with no CRD installation required. Pod-level config lives here.

    Validation: ``mode="agent-sandbox"`` requires ``template_name`` to be set.

    Attributes:
        mode: Backend mode — ``"agent-sandbox"`` or ``"raw"``.
        namespace: Kubernetes namespace where sandboxes are created.
        startup_timeout_seconds: Seconds to wait for a sandbox to become ready.
        default_exec_timeout: Default timeout (seconds) for individual
            ``execute()`` calls when no per-call timeout is given.

        default_labels: Labels applied to every sandbox, in addition to the
            automatic ``managed-by`` label.  Keys are automatically prefixed
            with ``langchain-kubernetes.bitkaio.com/``.
        ttl_seconds: Default absolute TTL from sandbox creation, in seconds.
            Per-call overrides on :meth:`get_or_create` take precedence.
        ttl_idle_seconds: Default idle TTL from the last ``execute()`` call, in
            seconds.  Per-call overrides on :meth:`get_or_create` take
            precedence.

        template_name: Name of the ``SandboxTemplate`` CR to use.
            **Required when** ``mode="agent-sandbox"``.
        connection_mode: How to connect to the sandbox-router (agent-sandbox only).

            - ``"tunnel"`` — auto port-forward via kubectl (default).
            - ``"gateway"`` — route through a Kubernetes Gateway resource.
            - ``"direct"`` — connect to an explicit URL.
        gateway_name: Gateway resource name. Only used with
            ``connection_mode="gateway"``.
        gateway_namespace: Namespace of the Gateway resource.
        api_url: Full URL of the sandbox-router. Only used with
            ``connection_mode="direct"``.
        server_port: Port the sandbox runtime listens on (agent-sandbox only).
        warm_pool_name: Name of a ``SandboxWarmPool`` resource to claim from
            (agent-sandbox mode only).
        kube_api_url: Kubernetes API server URL used for label-based lookups and
            annotation patching in agent-sandbox mode.  Defaults to the
            in-cluster URL ``https://kubernetes.default.svc.cluster.local``.
            For local development, point at ``kubectl proxy``:
            ``http://localhost:8001``.
        kube_token: Explicit bearer token for the Kubernetes API.  If omitted
            the in-cluster service-account token is read automatically.

        image: Container image for raw-mode Pods.
        image_pull_policy: Image pull policy for raw-mode Pods.
        image_pull_secrets: List of imagePullSecret names.
        workdir: Working directory inside the container.
        command: Pod entrypoint command (default: ``["sleep", "infinity"]``).
        env: Environment variables as ``{name: value}`` mapping.
        cpu_request: CPU request (e.g. ``"100m"``).
        cpu_limit: CPU limit.
        memory_request: Memory request (e.g. ``"256Mi"``).
        memory_limit: Memory limit.
        ephemeral_storage_limit: Ephemeral storage limit.
        block_network: If ``True``, create a deny-all NetworkPolicy (default).
        run_as_user: UID for the container process.
        run_as_group: GID for the container process.
        seccomp_profile: seccompProfile type (``"RuntimeDefault"`` or
            ``"Localhost"``).
        namespace_per_sandbox: If ``True``, create a dedicated namespace for
            each sandbox (enables stronger isolation at the cost of slower
            startup and more RBAC permissions).
        pod_ttl_seconds: Reserved for future use.
        service_account: ServiceAccount name for the sandbox Pod.
        node_selector: Node selector labels for scheduling.
        tolerations: List of toleration dicts.
        volumes: Additional volume definitions.
        volume_mounts: Additional volume mount definitions.
        init_containers: Init container definitions.
        pod_template_overrides: Free-form dict deep-merged into the Pod spec
            (use sparingly — overrides bypass validation).
        extra_annotations: Pod annotations.
        setup_script: Shell script run as the first exec command after sandbox
            creation. Useful for installing packages or bootstrapping state.
        warm_pool_size: Number of warm Pods to pre-create (raw mode only).
            ``0`` disables the warm pool.  When > 0 the provider pre-provisions
            this many idle Pods on first use and replenishes the pool after
            each delete.
    """

    # --- Mode selection ---
    mode: str = "agent-sandbox"

    # --- Shared ---
    namespace: str = "default"
    startup_timeout_seconds: int = 120
    default_exec_timeout: int = 60 * 30  # 30 minutes

    # --- Shared: labeling and TTL ---
    default_labels: dict[str, str] | None = None
    ttl_seconds: int | None = None
    ttl_idle_seconds: int | None = None

    # --- agent-sandbox mode fields ---
    template_name: str | None = None
    connection_mode: str = "tunnel"
    gateway_name: str | None = None
    gateway_namespace: str = "default"
    api_url: str | None = None
    server_port: int = 8888
    warm_pool_name: str | None = None
    kube_api_url: str | None = None
    kube_token: str | None = None

    # --- raw mode fields ---
    image: str = "python:3.12-slim"
    image_pull_policy: str = "IfNotPresent"
    image_pull_secrets: list[str] = field(default_factory=list)
    workdir: str = "/workspace"
    command: list[str] = field(default_factory=lambda: ["sleep", "infinity"])
    env: dict[str, str] = field(default_factory=dict)
    cpu_request: str = "100m"
    cpu_limit: str = "1000m"
    memory_request: str = "256Mi"
    memory_limit: str = "1Gi"
    ephemeral_storage_limit: str = "5Gi"
    block_network: bool = True
    run_as_user: int = 1000
    run_as_group: int = 1000
    seccomp_profile: str = "RuntimeDefault"
    namespace_per_sandbox: bool = False
    pod_ttl_seconds: int | None = 3600
    warm_pool_size: int = 0
    service_account: str | None = None
    node_selector: dict[str, str] = field(default_factory=dict)
    tolerations: list[dict] = field(default_factory=list)
    volumes: list[dict] = field(default_factory=list)
    volume_mounts: list[dict] = field(default_factory=list)
    init_containers: list[dict] = field(default_factory=list)
    pod_template_overrides: dict | None = None
    extra_annotations: dict[str, str] = field(default_factory=dict)
    setup_script: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in ("agent-sandbox", "raw"):
            raise ValueError(
                f"Unknown mode: {self.mode!r}. Must be 'agent-sandbox' or 'raw'."
            )
        if self.mode == "agent-sandbox" and self.template_name is None:
            raise ValueError(
                "template_name is required when mode='agent-sandbox'. "
                "Set KubernetesProviderConfig(template_name='my-template') or "
                "use mode='raw' for direct Pod management."
            )
