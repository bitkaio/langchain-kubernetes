"""KubernetesProvider: manages the lifecycle of KubernetesSandbox instances."""

from __future__ import annotations

import logging
from typing import Any

from deepagents.backends.protocol import SandboxBackendProtocol

from langchain_kubernetes._provider_base import (
    SandboxNotFoundError,
    SandboxProvider,
)

from langchain_kubernetes._utils import (
    LABEL_SANDBOX_ID,
    MANAGED_BY_SELECTOR,
    common_labels,
    make_namespace_name,
    make_pod_name,
    make_sandbox_id,
    poll_until,
)
from langchain_kubernetes.config import KubernetesProviderConfig
from langchain_kubernetes.manifests import (
    build_namespace_manifest,
    build_network_policy_manifest,
    build_pod_manifest,
)
from langchain_kubernetes.sandbox import KubernetesSandbox

logger = logging.getLogger(__name__)


def _build_k8s_clients(config: KubernetesProviderConfig):
    """Initialise and return ``(api_client, core_v1)``.

    Loads the kubeconfig (or in-cluster credentials when running inside a Pod)
    and constructs the ``CoreV1Api`` and ``NetworkingV1Api`` clients.

    Args:
        config: Provider configuration supplying kubeconfig path and context.

    Returns:
        Tuple of ``(kubernetes.client.ApiClient, kubernetes.client.CoreV1Api,
        kubernetes.client.NetworkingV1Api)``.
    """
    import kubernetes.client as k8s_client
    import kubernetes.config as k8s_config

    if config.kubeconfig:
        k8s_config.load_kube_config(
            config_file=config.kubeconfig,
            context=config.context,
        )
    else:
        try:
            k8s_config.load_incluster_config()
        except k8s_config.ConfigException:
            k8s_config.load_kube_config(context=config.context)

    api_client = k8s_client.ApiClient()
    core_v1 = k8s_client.CoreV1Api(api_client)
    networking_v1 = k8s_client.NetworkingV1Api(api_client)
    return api_client, core_v1, networking_v1


class KubernetesProvider(SandboxProvider):
    """Manages sandbox Pods on a Kubernetes cluster.

    Each sandbox is one ephemeral Pod running ``sleep infinity``.  When
    ``namespace_per_sandbox`` is ``True`` in *config*, every sandbox also
    gets its own Kubernetes namespace which is deleted on cleanup.

    Args:
        config: Optional provider configuration.  Defaults to
            :class:`~langchain_kubernetes.config.KubernetesProviderConfig`
            with all defaults.
    """

    def __init__(self, config: KubernetesProviderConfig | None = None) -> None:
        self._config = config or KubernetesProviderConfig()
        _, self._core_v1, self._networking_v1 = _build_k8s_clients(self._config)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_namespace(self, namespace: str, sandbox_id: str) -> None:
        """Create *namespace* if it does not exist.

        If the namespace already exists and carries deepagents labels, it is
        silently reused.  If it exists without the expected labels a
        :class:`RuntimeError` is raised.

        Args:
            namespace: Namespace name to create or verify.
            sandbox_id: Sandbox ID for labelling.

        Raises:
            RuntimeError: When the namespace exists but is not managed by
                deepagents.
        """
        import kubernetes.client.exceptions as k8s_exc

        manifest = build_namespace_manifest(namespace, sandbox_id)
        try:
            import kubernetes.client as k8s_client

            body = k8s_client.V1Namespace(
                metadata=k8s_client.V1ObjectMeta(
                    name=namespace,
                    labels=manifest["metadata"]["labels"],
                )
            )
            self._core_v1.create_namespace(body)
            logger.debug("Created namespace %s", namespace)
        except k8s_exc.ApiException as exc:
            if exc.status == 409:
                # Already exists — verify it is managed by deepagents
                ns = self._core_v1.read_namespace(namespace)
                labels = (ns.metadata.labels or {}) if ns.metadata else {}
                if labels.get("app.kubernetes.io/managed-by") != "deepagents":
                    raise RuntimeError(
                        f"Namespace '{namespace}' already exists but is not managed by deepagents"
                    ) from exc
                logger.debug("Namespace %s already exists, reusing", namespace)
            else:
                raise

    def _create_network_policy(self, namespace: str, sandbox_id: str) -> None:
        """Attach a deny-all NetworkPolicy to the sandbox Pod.

        Args:
            namespace: Namespace to create the policy in.
            sandbox_id: Sandbox identifier (used for name and pod-selector).
        """
        import kubernetes.client as k8s_client
        import kubernetes.client.exceptions as k8s_exc

        manifest = build_network_policy_manifest(namespace=namespace, sandbox_id=sandbox_id)
        spec = manifest["spec"]
        body = k8s_client.V1NetworkPolicy(
            metadata=k8s_client.V1ObjectMeta(
                name=manifest["metadata"]["name"],
                namespace=namespace,
                labels=manifest["metadata"]["labels"],
            ),
            spec=k8s_client.V1NetworkPolicySpec(
                pod_selector=k8s_client.V1LabelSelector(
                    match_labels=spec["podSelector"]["matchLabels"]
                ),
                policy_types=spec["policyTypes"],
                ingress=[],
                egress=[],
            ),
        )
        try:
            self._networking_v1.create_namespaced_network_policy(namespace, body)
            logger.debug("Created NetworkPolicy for sandbox %s", sandbox_id)
        except k8s_exc.ApiException as exc:
            if exc.status != 409:
                raise

    def _create_pod(self, pod_name: str, namespace: str, sandbox_id: str) -> None:
        """Create the sandbox Pod in *namespace*.

        Args:
            pod_name: Pod name.
            namespace: Target namespace.
            sandbox_id: Sandbox identifier used for labels.
        """
        import kubernetes.client as k8s_client

        manifest = build_pod_manifest(
            pod_name=pod_name,
            namespace=namespace,
            sandbox_id=sandbox_id,
            config=self._config,
        )
        container_spec = manifest["spec"]["containers"][0]
        security_ctx = container_spec["securityContext"]

        container = k8s_client.V1Container(
            name=container_spec["name"],
            image=container_spec["image"],
            image_pull_policy=container_spec["imagePullPolicy"],
            command=container_spec["command"],
            env=[
                k8s_client.V1EnvVar(name=e["name"], value=e["value"])
                for e in container_spec.get("env", [])
            ],
            resources=k8s_client.V1ResourceRequirements(
                requests=container_spec.get("resources", {}).get("requests"),
                limits=container_spec.get("resources", {}).get("limits"),
            )
            if container_spec.get("resources")
            else None,
            security_context=k8s_client.V1SecurityContext(
                allow_privilege_escalation=security_ctx["allowPrivilegeEscalation"],
                run_as_non_root=security_ctx["runAsNonRoot"],
                run_as_user=security_ctx["runAsUser"],
                run_as_group=security_ctx["runAsGroup"],
                capabilities=k8s_client.V1Capabilities(
                    drop=security_ctx["capabilities"]["drop"]
                ),
                seccomp_profile=k8s_client.V1SeccompProfile(
                    type=security_ctx["seccompProfile"]["type"]
                ),
            ),
        )

        pod = k8s_client.V1Pod(
            metadata=k8s_client.V1ObjectMeta(
                name=pod_name,
                namespace=namespace,
                labels=manifest["metadata"]["labels"],
            ),
            spec=k8s_client.V1PodSpec(
                restart_policy="Never",
                automount_service_account_token=False,
                containers=[container],
            ),
        )
        self._core_v1.create_namespaced_pod(namespace, pod)
        logger.debug("Created Pod %s/%s", namespace, pod_name)

    def _wait_for_pod_running(self, pod_name: str, namespace: str) -> None:
        """Block until the Pod phase is ``Running``.

        Args:
            pod_name: Pod to watch.
            namespace: Namespace of the Pod.

        Raises:
            TimeoutError: If the Pod does not reach ``Running`` within
                :attr:`~langchain_kubernetes.config.KubernetesProviderConfig.startup_timeout`
                seconds.
        """

        def _is_running() -> bool:
            pod = self._core_v1.read_namespaced_pod(pod_name, namespace)
            phase = pod.status.phase if pod.status else None
            if phase == "Failed":
                raise RuntimeError(f"Pod {namespace}/{pod_name} entered Failed phase during startup")
            return phase == "Running"

        def _cleanup():
            try:
                self._core_v1.delete_namespaced_pod(pod_name, namespace)
            except Exception:
                pass

        poll_until(
            _is_running,
            timeout=self._config.startup_timeout,
            on_timeout=_cleanup,
        )

    def _parse_sandbox_id(self, sandbox_id: str) -> tuple[str, str]:
        """Parse a sandbox ID into ``(namespace, pod_name)``.

        Args:
            sandbox_id: Either ``"pod-name"`` or ``"namespace/pod-name"``.

        Returns:
            Tuple of ``(namespace, pod_name)``.
        """
        if "/" in sandbox_id:
            namespace, pod_name = sandbox_id.split("/", 1)
        else:
            namespace = self._config.namespace
            pod_name = sandbox_id
        return namespace, pod_name

    def _sandbox_from_pod(self, pod_name: str, namespace: str) -> KubernetesSandbox:
        return KubernetesSandbox(
            pod_name=pod_name,
            namespace=namespace,
            container=self._config.container_name,
            core_v1=self._core_v1,
            config=self._config,
        )

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

        When *sandbox_id* is given the method attempts to reconnect to the
        matching Pod.  If the Pod no longer exists a
        :class:`~deepagents_cli.integrations.sandbox_provider.SandboxNotFoundError`
        is raised.

        When *sandbox_id* is ``None`` a fresh Pod is created (and optionally
        a dedicated namespace + NetworkPolicy).

        Args:
            sandbox_id: Existing sandbox identifier to reconnect to.
            **kwargs: Unused; present for interface compatibility.

        Returns:
            :class:`~langchain_kubernetes.sandbox.KubernetesSandbox` instance.

        Raises:
            SandboxNotFoundError: When the requested sandbox ID does not exist.
            RuntimeError: On Pod startup failure or namespace conflict.
            TimeoutError: If the Pod does not become Ready in time.
        """
        import kubernetes.client.exceptions as k8s_exc

        if sandbox_id is not None:
            # Reconnect path
            namespace, pod_name = self._parse_sandbox_id(sandbox_id)
            try:
                pod = self._core_v1.read_namespaced_pod(pod_name, namespace)
            except k8s_exc.ApiException as exc:
                if exc.status == 404:
                    raise SandboxNotFoundError(
                        f"Sandbox '{sandbox_id}' not found (Pod {namespace}/{pod_name} does not exist)"
                    ) from exc
                raise
            phase = pod.status.phase if pod.status else None
            if phase != "Running":
                raise SandboxNotFoundError(
                    f"Sandbox '{sandbox_id}' exists but Pod is in phase '{phase}' (expected 'Running')"
                )
            logger.info("Reconnected to sandbox %s", sandbox_id)
            return self._sandbox_from_pod(pod_name, namespace)

        # Create path
        new_id = make_sandbox_id()
        pod_name = make_pod_name(new_id)

        if self._config.namespace_per_sandbox:
            namespace = make_namespace_name(new_id)
            self._ensure_namespace(namespace, new_id)
        else:
            namespace = self._config.namespace
            self._ensure_namespace(namespace, new_id)

        if self._config.block_network:
            self._create_network_policy(namespace, new_id)

        self._create_pod(pod_name, namespace, new_id)

        try:
            self._wait_for_pod_running(pod_name, namespace)
        except TimeoutError:
            # cleanup already attempted inside _wait_for_pod_running
            raise

        logger.info("Created sandbox %s (pod %s/%s)", new_id, namespace, pod_name)
        return self._sandbox_from_pod(pod_name, namespace)

    def list(self, cursor: str | None = None, **kwargs: Any) -> list[SandboxBackendProtocol]:
        """List all active deepagents sandbox Pods.

        Args:
            cursor: Unused; present for interface compatibility.
            **kwargs: Unused.

        Returns:
            List of :class:`~langchain_kubernetes.sandbox.KubernetesSandbox`
            instances for all Pods with status ``Running`` (or ``Pending``).
        """
        pods = self._core_v1.list_pod_for_all_namespaces(
            label_selector=MANAGED_BY_SELECTOR
        )
        sandboxes: list[SandboxBackendProtocol] = []
        for pod in pods.items:
            phase = pod.status.phase if pod.status else None
            if phase not in ("Running", "Pending"):
                continue
            sandbox = self._sandbox_from_pod(
                pod.metadata.name,
                pod.metadata.namespace,
            )
            sandboxes.append(sandbox)
        return sandboxes

    def delete(self, *, sandbox_id: str, **kwargs: Any) -> None:
        """Delete a sandbox Pod (and its namespace when applicable).

        Idempotent: deleting a non-existent sandbox is a no-op.

        Args:
            sandbox_id: Sandbox identifier to delete.
            **kwargs: Unused.
        """
        import kubernetes.client as k8s_client
        import kubernetes.client.exceptions as k8s_exc

        namespace, pod_name = self._parse_sandbox_id(sandbox_id)

        if self._config.namespace_per_sandbox:
            # Deleting the namespace cascades to all resources inside it
            try:
                self._core_v1.delete_namespace(
                    namespace,
                    body=k8s_client.V1DeleteOptions(propagation_policy="Foreground"),
                )
                logger.info("Deleted namespace %s (sandbox %s)", namespace, sandbox_id)
            except k8s_exc.ApiException as exc:
                if exc.status != 404:
                    raise
        else:
            # Delete the Pod and (optionally) the NetworkPolicy
            try:
                self._core_v1.delete_namespaced_pod(
                    pod_name,
                    namespace,
                    body=k8s_client.V1DeleteOptions(propagation_policy="Background"),
                )
                logger.info("Deleted Pod %s/%s (sandbox %s)", namespace, pod_name, sandbox_id)
            except k8s_exc.ApiException as exc:
                if exc.status != 404:
                    raise

            if self._config.block_network:
                policy_name = f"deepagents-sandbox-deny-all-{pod_name.removeprefix('deepagents-sandbox-')}"
                try:
                    self._networking_v1.delete_namespaced_network_policy(
                        policy_name, namespace
                    )
                except k8s_exc.ApiException as exc:
                    if exc.status != 404:
                        raise

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
        import asyncio

        return await asyncio.to_thread(
            self.get_or_create, sandbox_id=sandbox_id, **kwargs
        )

    async def alist(self, cursor: str | None = None, **kwargs: Any) -> list[SandboxBackendProtocol]:
        """Async wrapper around :meth:`list`.

        Args:
            cursor: Unused.
            **kwargs: Unused.

        Returns:
            List of active sandboxes.
        """
        import asyncio

        return await asyncio.to_thread(self.list, cursor=cursor, **kwargs)

    async def adelete(self, *, sandbox_id: str, **kwargs: Any) -> None:
        """Async wrapper around :meth:`delete`.

        Args:
            sandbox_id: Sandbox to delete.
            **kwargs: Unused.
        """
        import asyncio

        await asyncio.to_thread(self.delete, sandbox_id=sandbox_id, **kwargs)
