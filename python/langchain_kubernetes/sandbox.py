"""KubernetesSandbox: a BaseSandbox backed by an ephemeral Kubernetes Pod."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

from langchain_kubernetes.exec_transport import (
    download_files_tar,
    exec_command,
    upload_files_tar,
)

if TYPE_CHECKING:
    import kubernetes.client

    from langchain_kubernetes.config import KubernetesProviderConfig

logger = logging.getLogger(__name__)


class KubernetesSandbox(BaseSandbox):
    """A sandbox backed by a running Kubernetes Pod.

    The Pod must already exist and be in the ``Running`` phase before this
    object is constructed.  Use
    :class:`~langchain_kubernetes.provider.KubernetesProvider` to create and
    manage the Pod lifecycle.

    All file-system operations (``read``, ``write``, ``edit``, ``ls_info``,
    ``glob_info``, ``grep_raw``) are inherited from
    :class:`~deepagents.backends.sandbox.BaseSandbox` and are implemented by
    constructing shell commands and delegating to :meth:`execute`.

    Args:
        pod_name: Name of the running Pod.
        namespace: Kubernetes namespace that contains the Pod.
        container: Name of the container inside the Pod.
        core_v1: Authenticated ``kubernetes.client.CoreV1Api`` instance.
        config: Provider configuration (used for default exec timeout).
    """

    def __init__(
        self,
        *,
        pod_name: str,
        namespace: str,
        container: str,
        core_v1: "kubernetes.client.CoreV1Api",
        config: "KubernetesProviderConfig",
    ) -> None:
        self._pod_name = pod_name
        self._namespace = namespace
        self._container = container
        self._core_v1 = core_v1
        self._config = config

    # ------------------------------------------------------------------
    # SandboxBackendProtocol: id
    # ------------------------------------------------------------------

    @property
    def id(self) -> str:
        """Unique sandbox identifier.

        Returns:
            ``"{namespace}/{pod_name}"`` when namespace-per-sandbox mode is
            active, otherwise just ``pod_name``.
        """
        if self._config.namespace_per_sandbox:
            return f"{self._namespace}/{self._pod_name}"
        return self._pod_name

    # ------------------------------------------------------------------
    # BaseSandbox: execute (sync)
    # ------------------------------------------------------------------

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Execute *command* inside the sandbox Pod and return the result.

        Args:
            command: Shell command string to run via ``/bin/sh -c``.
            timeout: Override the default exec timeout (seconds).  When
                ``None`` the :attr:`~langchain_kubernetes.config.KubernetesProviderConfig.default_exec_timeout`
                from the provider config is used.

        Returns:
            :class:`~deepagents.backends.protocol.ExecuteResponse` with
            combined stdout/stderr, the process exit code, and
            ``truncated=False``.
        """
        effective_timeout = timeout if timeout is not None else self._config.default_exec_timeout
        logger.debug("exec [%s/%s] %s", self._namespace, self._pod_name, command[:120])
        return exec_command(
            self._core_v1,
            pod_name=self._pod_name,
            namespace=self._namespace,
            container=self._container,
            command=command,
            timeout=effective_timeout,
        )

    # ------------------------------------------------------------------
    # BaseSandbox: execute (async)
    # ------------------------------------------------------------------

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Async variant of :meth:`execute`.

        Requires ``kubernetes_asyncio`` (``pip install 'langchain-kubernetes[async]'``).

        Args:
            command: Shell command string.
            timeout: Per-call timeout override (seconds).

        Returns:
            :class:`~deepagents.backends.protocol.ExecuteResponse`.
        """
        from langchain_kubernetes.exec_transport import async_exec_command

        try:
            from kubernetes_asyncio import client as async_k8s_client
            from kubernetes_asyncio import config as async_k8s_config
        except ImportError as exc:
            raise ImportError(
                "kubernetes_asyncio is required for async exec. "
                "Install it with: pip install 'langchain-kubernetes[async]'"
            ) from exc

        effective_timeout = timeout if timeout is not None else self._config.default_exec_timeout

        # Build an async CoreV1Api using the same kubeconfig context as the
        # sync client (load config then create client).
        if self._config.kubeconfig:
            await async_k8s_config.load_kube_config(
                config_file=self._config.kubeconfig,
                context=self._config.context,
            )
        else:
            try:
                async_k8s_config.load_incluster_config()
            except Exception:
                await async_k8s_config.load_kube_config(context=self._config.context)

        async with async_k8s_client.ApiClient() as api_client:
            core_v1_async = async_k8s_client.CoreV1Api(api_client)
            return await async_exec_command(
                core_v1_async,
                pod_name=self._pod_name,
                namespace=self._namespace,
                container=self._container,
                command=command,
                timeout=effective_timeout,
            )

    # ------------------------------------------------------------------
    # BaseSandbox: file transfer (override with tar-based implementation)
    # ------------------------------------------------------------------

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Upload files to the sandbox Pod via a tar-piped exec.

        Falls back to the :class:`~deepagents.backends.sandbox.BaseSandbox`
        base64-via-execute path on error.

        Args:
            files: List of ``(absolute_path, content_bytes)`` tuples.

        Returns:
            List of :class:`~deepagents.backends.protocol.FileUploadResponse`.
        """
        try:
            return upload_files_tar(
                self._core_v1,
                pod_name=self._pod_name,
                namespace=self._namespace,
                container=self._container,
                files=files,
            )
        except Exception as exc:
            logger.warning("tar upload failed (%s), falling back to base64 path", exc)
            return super().upload_files(files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download files from the sandbox Pod via a tar-streamed exec.

        Falls back to the :class:`~deepagents.backends.sandbox.BaseSandbox`
        base64-via-execute path on error.

        Args:
            paths: Absolute paths to download from the Pod.

        Returns:
            List of :class:`~deepagents.backends.protocol.FileDownloadResponse`.
        """
        try:
            return download_files_tar(
                self._core_v1,
                pod_name=self._pod_name,
                namespace=self._namespace,
                container=self._container,
                paths=paths,
            )
        except Exception as exc:
            logger.warning("tar download failed (%s), falling back to base64 path", exc)
            return super().download_files(paths)
