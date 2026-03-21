"""RawK8sBackend — direct Pod management via the Kubernetes API.

Does not require the agent-sandbox controller or any CRDs. Manages ephemeral
Pods directly, using WebSocket exec for command execution and base64-encoded
tar streams for file transfer.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from deepagents.backends.protocol import ExecuteResponse, FileDownloadResponse, FileUploadResponse

from langchain_kubernetes._labels import (
    ANN_LAST_ACTIVITY,
    LABEL_POOL_STATUS,
    MANAGED_SELECTOR,
    POOL_STATUS_ACTIVE,
    now_iso,
)
from langchain_kubernetes._provider_base import SandboxNotFoundError
from langchain_kubernetes._utils import generate_sandbox_id
from langchain_kubernetes.backends.raw_manifests import (
    build_namespace_manifest,
    build_network_policy_manifest,
    build_pod_manifest,
)
from langchain_kubernetes.backends.raw_transport import (
    download_files_tar,
    exec_command,
    upload_files_tar,
)

if TYPE_CHECKING:
    from langchain_kubernetes.config import KubernetesProviderConfig

logger = logging.getLogger(__name__)

try:
    from kubernetes.client.exceptions import ApiException as _ApiException
except ImportError:

    class _ApiException(Exception):  # type: ignore[assignment]
        """Fallback stub used when the ``kubernetes`` package is not installed."""

        def __init__(self, status: int = 0, reason: str = "", **kwargs: Any) -> None:
            self.status = status
            self.reason = reason
            super().__init__(f"({status})\nReason: {reason}")

_POD_POLL_INTERVAL = 2  # seconds between phase polls


class RawK8sBackend:
    """Backend that directly manages ephemeral Kubernetes Pods.

    Works on any cluster without additional CRDs or controllers. All work
    happens via the Kubernetes exec API — the Pod's entrypoint is
    ``["sleep", "infinity"]``.

    Do not construct directly. Use :meth:`create` or :meth:`reconnect` to
    obtain an instance with a live Pod.

    Args:
        sandbox_id: Short unique sandbox identifier.
        pod_name: Kubernetes Pod name (``deepagents-<sandbox_id>``).
        namespace: Namespace containing the Pod.
        container: Container name within the Pod (``"sandbox"``).
        core_v1: ``kubernetes.client.CoreV1Api`` instance.
        networking_v1: ``kubernetes.client.NetworkingV1Api`` instance.
        config: Provider configuration.
    """

    def __init__(
        self,
        *,
        sandbox_id: str,
        pod_name: str,
        namespace: str,
        container: str,
        core_v1: Any,
        networking_v1: Any,
        config: "KubernetesProviderConfig",
        ttl_idle_seconds: int | None = None,
    ) -> None:
        self._sandbox_id = sandbox_id
        self._pod_name = pod_name
        self._namespace = namespace
        self._container = container
        self._core_v1 = core_v1
        self._networking_v1 = networking_v1
        self._config = config
        self._ttl_idle_seconds = ttl_idle_seconds

    # ------------------------------------------------------------------
    # Protocol: id
    # ------------------------------------------------------------------

    @property
    def id(self) -> str:
        """Short unique sandbox identifier.

        Returns:
            The sandbox ID string used in Pod labels and as the provider key.
        """
        return self._sandbox_id

    # ------------------------------------------------------------------
    # Protocol: execute
    # ------------------------------------------------------------------

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Run *command* in the sandbox Pod via the Kubernetes exec API.

        Args:
            command: Shell command string (executed via ``/bin/sh -c``).
            timeout: Per-call timeout in seconds.  Defaults to
                ``config.default_exec_timeout``.

        Returns:
            :class:`~deepagents.backends.protocol.ExecuteResponse`.
        """
        effective_timeout = (
            timeout if timeout is not None else self._config.default_exec_timeout
        )
        logger.debug("exec [%s] %s", self._sandbox_id, command[:120])
        output, exit_code, truncated = exec_command(
            self._core_v1,
            self._pod_name,
            self._namespace,
            self._container,
            command,
            effective_timeout,
        )
        result = ExecuteResponse(output=output, exit_code=exit_code, truncated=truncated)
        if self._ttl_idle_seconds is not None:
            self._update_last_activity()
        return result

    def _update_last_activity(self) -> None:
        """Patch the Pod annotation with the current UTC time (fire-and-forget)."""
        try:
            patch = {"metadata": {"annotations": {ANN_LAST_ACTIVITY: now_iso()}}}
            self._core_v1.patch_namespaced_pod(
                name=self._pod_name,
                namespace=self._namespace,
                body=patch,
            )
        except Exception as exc:
            logger.warning(
                "Failed to update last-activity annotation on Pod %s: %s",
                self._pod_name,
                exc,
            )

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Async variant — runs :meth:`execute` in a thread pool.

        Args:
            command: Shell command string.
            timeout: Per-call timeout in seconds.

        Returns:
            :class:`~deepagents.backends.protocol.ExecuteResponse`.
        """
        return await asyncio.to_thread(self.execute, command, timeout=timeout)

    # ------------------------------------------------------------------
    # Protocol: file transfer
    # ------------------------------------------------------------------

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Upload files via base64-encoded tar stream.

        Args:
            files: List of ``(absolute_path, content_bytes)`` tuples.

        Returns:
            List of :class:`~deepagents.backends.protocol.FileUploadResponse`.

        Raises:
            RuntimeError: If the tar extraction exits non-zero.
        """
        return upload_files_tar(
            self._core_v1,
            self._pod_name,
            self._namespace,
            self._container,
            files,
            timeout=self._config.default_exec_timeout,
        )

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download files via base64-encoded tar stream.

        Args:
            paths: Absolute paths to download from the sandbox.

        Returns:
            List of :class:`~deepagents.backends.protocol.FileDownloadResponse`.
        """
        return download_files_tar(
            self._core_v1,
            self._pod_name,
            self._namespace,
            self._container,
            paths,
            timeout=self._config.default_exec_timeout,
        )

    # ------------------------------------------------------------------
    # Protocol: cleanup
    # ------------------------------------------------------------------

    def cleanup(self) -> None:
        """Delete the sandbox Pod and associated NetworkPolicy.

        If ``namespace_per_sandbox=True``, deletes the entire Namespace
        (which cascades to all resources in it). Otherwise, deletes only the
        Pod and its NetworkPolicy.

        Idempotent — 404 responses are silently ignored.
        """
        logger.info("Cleaning up raw sandbox %s", self._sandbox_id)
        try:
            if self._config.namespace_per_sandbox:
                self._delete_namespace(self._namespace)
            else:
                self._delete_pod()
                self._delete_network_policy()
        except Exception as exc:
            logger.warning("Cleanup error for sandbox %s: %s", self._sandbox_id, exc)

    # ------------------------------------------------------------------
    # Factory class methods
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        config: "KubernetesProviderConfig",
        sandbox_id: str | None = None,
        extra_labels: dict[str, Any] | None = None,
        extra_annotations: dict[str, str] | None = None,
        ttl_idle_seconds: int | None = None,
    ) -> "RawK8sBackend":
        """Provision a new Pod and return a ``RawK8sBackend`` for it.

        Steps:
        1. Load kubeconfig (in-cluster first, then local).
        2. Generate a sandbox ID if not provided.
        3. Optionally create a per-sandbox Namespace.
        4. Create the Pod.
        5. If ``block_network=True``, create a deny-all NetworkPolicy.
        6. Wait for the Pod to enter the ``Running`` phase.

        Args:
            config: Provider configuration.
            sandbox_id: Pre-assigned sandbox ID. ``None`` generates a new one.

        Returns:
            ``RawK8sBackend`` backed by a running Pod.

        Raises:
            ImportError: If the ``kubernetes`` package is not installed.
            TimeoutError: If the Pod does not reach ``Running`` within
                ``startup_timeout_seconds``.
            RuntimeError: On Pod creation failure.
        """
        core_v1, networking_v1 = _load_k8s_clients()

        if sandbox_id is None:
            sandbox_id = generate_sandbox_id()

        namespace = _resolve_namespace(config, sandbox_id)

        if config.namespace_per_sandbox:
            _create_namespace(core_v1, namespace)

        pod_name = f"deepagents-{sandbox_id}"
        pod_manifest = build_pod_manifest(
            config, sandbox_id,
            extra_labels=extra_labels,
            extra_annotations=extra_annotations,
        )

        logger.info(
            "Creating sandbox Pod %s in namespace %s", pod_name, namespace
        )
        try:
            core_v1.create_namespaced_pod(namespace=namespace, body=pod_manifest)
        except Exception as exc:
            # Best-effort cleanup before re-raising
            _try_delete_pod(core_v1, pod_name, namespace)
            raise RuntimeError(
                f"Failed to create Pod {pod_name!r} in namespace {namespace!r}: {exc}"
            ) from exc

        if config.block_network:
            _create_network_policy(networking_v1, sandbox_id, namespace)

        try:
            _wait_for_pod_running(
                core_v1, pod_name, namespace, timeout=config.startup_timeout_seconds
            )
        except Exception as exc:
            _try_delete_pod(core_v1, pod_name, namespace)
            raise

        return cls(
            sandbox_id=sandbox_id,
            pod_name=pod_name,
            namespace=namespace,
            container="sandbox",
            core_v1=core_v1,
            networking_v1=networking_v1,
            config=config,
            ttl_idle_seconds=ttl_idle_seconds,
        )

    @classmethod
    def claim_warm_pod(
        cls,
        config: "KubernetesProviderConfig",
        extra_labels: dict[str, Any] | None = None,
        extra_annotations: dict[str, str] | None = None,
        ttl_idle_seconds: int | None = None,
    ) -> "RawK8sBackend | None":
        """Claim a warm Pod from the pool by updating its labels.

        Args:
            config: Provider configuration.
            extra_labels: Additional labels to merge onto the Pod.
            extra_annotations: Additional annotations to merge onto the Pod.
            ttl_idle_seconds: Idle TTL to attach to the backend.

        Returns:
            Backend wrapping the claimed Pod, or ``None`` if no warm Pod available.
        """
        from langchain_kubernetes._labels import POOL_STATUS_WARM, warm_pool_selector

        core_v1, networking_v1 = _load_k8s_clients()
        try:
            pod_list = core_v1.list_namespaced_pod(
                namespace=config.namespace,
                label_selector=warm_pool_selector(),
            )
        except Exception as exc:
            logger.warning("Failed to list warm pool Pods: %s", exc)
            return None

        for pod in (pod_list.items or []):
            if pod.status.phase not in ("Running", "Pending"):
                continue
            pod_name = pod.metadata.name
            namespace = pod.metadata.namespace or config.namespace

            patch_labels: dict[str, Any] = {
                LABEL_POOL_STATUS: POOL_STATUS_ACTIVE,
                **(extra_labels or {}),
            }
            patch_annotations = dict(extra_annotations or {})
            patch: dict[str, Any] = {"metadata": {"labels": patch_labels}}
            if patch_annotations:
                patch["metadata"]["annotations"] = patch_annotations

            try:
                core_v1.patch_namespaced_pod(
                    name=pod_name,
                    namespace=namespace,
                    body=patch,
                )
            except Exception as exc:
                logger.warning("Failed to claim warm Pod %s: %s", pod_name, exc)
                continue

            from langchain_kubernetes.backends.raw_manifests import LABEL_SANDBOX_ID

            labels = pod.metadata.labels or {}
            sandbox_id = labels.get(LABEL_SANDBOX_ID, pod_name.removeprefix("deepagents-"))
            logger.info("Claimed warm Pod %s", pod_name)
            return cls(
                sandbox_id=sandbox_id,
                pod_name=pod_name,
                namespace=namespace,
                container="sandbox",
                core_v1=core_v1,
                networking_v1=networking_v1,
                config=config,
                ttl_idle_seconds=ttl_idle_seconds,
            )

        return None

    @classmethod
    def reconnect(
        cls,
        config: "KubernetesProviderConfig",
        sandbox_id: str,
    ) -> "RawK8sBackend":
        """Reconnect to an existing Pod by sandbox ID.

        Looks up the Pod by name and verifies it is in the ``Running`` phase.

        Args:
            config: Provider configuration.
            sandbox_id: Sandbox ID to reconnect to.

        Returns:
            ``RawK8sBackend`` backed by the existing Pod.

        Raises:
            SandboxNotFoundError: If the Pod does not exist.
            RuntimeError: If the Pod is not in ``Running`` phase.
        """
        core_v1, networking_v1 = _load_k8s_clients()
        pod_name = f"deepagents-{sandbox_id}"
        namespace = _resolve_namespace(config, sandbox_id)

        try:
            pod = core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
        except _ApiException as exc:
            if exc.status == 404:
                raise SandboxNotFoundError(
                    f"Pod '{pod_name}' not found in namespace '{namespace}'. "
                    f"Sandbox '{sandbox_id}' may have been deleted."
                ) from exc
            raise

        phase = pod.status.phase if pod.status else None
        if phase != "Running":
            raise RuntimeError(
                f"Pod '{pod_name}' is in phase '{phase}', expected 'Running'."
            )

        return cls(
            sandbox_id=sandbox_id,
            pod_name=pod_name,
            namespace=namespace,
            container="sandbox",
            core_v1=core_v1,
            networking_v1=networking_v1,
            config=config,
        )

    # ------------------------------------------------------------------
    # Static helpers for provider-level operations
    # ------------------------------------------------------------------

    @staticmethod
    def load_k8s_clients() -> tuple[Any, Any]:
        """Load and return ``(CoreV1Api, NetworkingV1Api)`` — public alias."""
        return _load_k8s_clients()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _delete_pod(self) -> None:
        try:
            self._core_v1.delete_namespaced_pod(
                name=self._pod_name, namespace=self._namespace
            )
            logger.debug("Deleted Pod %s", self._pod_name)
        except _ApiException as exc:
            if exc.status != 404:
                raise

    def _delete_network_policy(self) -> None:
        np_name = f"deepagents-deny-all-{self._sandbox_id}"
        try:
            self._networking_v1.delete_namespaced_network_policy(
                name=np_name, namespace=self._namespace
            )
            logger.debug("Deleted NetworkPolicy %s", np_name)
        except _ApiException as exc:
            if exc.status != 404:
                raise

    def _delete_namespace(self, namespace: str) -> None:
        try:
            self._core_v1.delete_namespace(name=namespace)
            logger.debug("Deleted Namespace %s", namespace)
        except _ApiException as exc:
            if exc.status != 404:
                raise


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _load_k8s_clients() -> tuple[Any, Any]:
    """Load kubeconfig and return (CoreV1Api, NetworkingV1Api).

    Tries in-cluster config first, then local kubeconfig.

    Returns:
        Tuple of ``(core_v1, networking_v1)`` API client instances.

    Raises:
        ImportError: If ``kubernetes`` is not installed.
    """
    try:
        from kubernetes import client as k8s_client
        from kubernetes import config as k8s_config
    except ImportError as exc:
        raise ImportError(
            "Raw Kubernetes mode requires the 'kubernetes' package. "
            "Install with: pip install langchain-kubernetes[raw]"
        ) from exc

    try:
        k8s_config.load_incluster_config()
    except Exception:
        k8s_config.load_kube_config()

    return k8s_client.CoreV1Api(), k8s_client.NetworkingV1Api()


def _resolve_namespace(config: "KubernetesProviderConfig", sandbox_id: str) -> str:
    """Return the effective namespace for *sandbox_id*.

    Args:
        config: Provider configuration.
        sandbox_id: Sandbox identifier.

    Returns:
        Namespace name.
    """
    if config.namespace_per_sandbox:
        return f"deepagents-{sandbox_id}"
    return config.namespace


def _create_namespace(core_v1: Any, name: str) -> None:
    manifest = build_namespace_manifest(name)
    try:
        core_v1.create_namespace(body=manifest)
        logger.info("Created Namespace %s", name)
    except _ApiException as exc:
        if exc.status != 409:  # 409 = already exists
            raise


def _create_network_policy(networking_v1: Any, sandbox_id: str, namespace: str) -> None:
    manifest = build_network_policy_manifest(sandbox_id, namespace)
    try:
        networking_v1.create_namespaced_network_policy(namespace=namespace, body=manifest)
        logger.debug("Created NetworkPolicy for sandbox %s", sandbox_id)
    except _ApiException as exc:
        if exc.status == 409:
            logger.debug("NetworkPolicy for %s already exists", sandbox_id)
        else:
            logger.warning("Failed to create NetworkPolicy for %s: %s", sandbox_id, exc)


def _wait_for_pod_running(
    core_v1: Any, pod_name: str, namespace: str, timeout: int
) -> None:
    """Poll until the Pod reaches ``Running`` phase or timeout.

    Args:
        core_v1: ``CoreV1Api`` instance.
        pod_name: Pod to watch.
        namespace: Pod namespace.
        timeout: Maximum seconds to wait.

    Raises:
        TimeoutError: If the Pod does not become Running in time.
        RuntimeError: If the Pod enters a terminal failed state.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            pod = core_v1.read_namespaced_pod(name=pod_name, namespace=namespace)
            phase = pod.status.phase if pod.status else None
            if phase == "Running":
                logger.info("Pod %s is Running", pod_name)
                return
            if phase in ("Failed", "Unknown"):
                raise RuntimeError(
                    f"Pod '{pod_name}' entered terminal phase '{phase}' during startup."
                )
            # "Pending" or "ContainerCreating" — keep polling
            logger.debug("Pod %s phase: %s — waiting...", pod_name, phase)
        except _ApiException as exc:
            if exc.status == 404:
                logger.debug("Pod %s not yet visible, waiting...", pod_name)
            else:
                raise

        time.sleep(_POD_POLL_INTERVAL)

    raise TimeoutError(
        f"Pod '{pod_name}' did not reach Running phase within {timeout}s. "
        "Check Pod events: kubectl describe pod -n "
        f"{namespace} {pod_name}"
    )


def _try_delete_pod(core_v1: Any, pod_name: str, namespace: str) -> None:
    """Best-effort Pod deletion — errors are logged and swallowed."""
    try:
        core_v1.delete_namespaced_pod(name=pod_name, namespace=namespace)
    except Exception as exc:
        logger.debug("Best-effort Pod cleanup failed for %s: %s", pod_name, exc)
