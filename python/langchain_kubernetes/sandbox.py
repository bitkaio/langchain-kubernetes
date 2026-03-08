"""KubernetesSandbox: a BaseSandbox backed by a KubernetesBackendProtocol."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

if TYPE_CHECKING:
    from langchain_kubernetes.backends.protocol import KubernetesBackendProtocol

logger = logging.getLogger(__name__)


class KubernetesSandbox(BaseSandbox):
    """A DeepAgents sandbox backed by a Kubernetes execution environment.

    Delegates all operations to a
    :class:`~langchain_kubernetes.backends.protocol.KubernetesBackendProtocol`
    implementation — either
    :class:`~langchain_kubernetes.backends.agent_sandbox.AgentSandboxBackend`
    (when ``mode="agent-sandbox"``) or
    :class:`~langchain_kubernetes.backends.raw.RawK8sBackend`
    (when ``mode="raw"``).

    The sandbox does not know which mode is active. Use
    :class:`~langchain_kubernetes.provider.KubernetesProvider` to create
    instances with the right backend.

    All filesystem helper methods (``read``, ``write``, ``edit``, ``ls_info``,
    ``glob_info``, ``grep_raw``) are inherited from
    :class:`~deepagents.backends.sandbox.BaseSandbox` — they work by
    constructing shell commands and calling :meth:`execute`.

    File upload and download first try the backend's native transfer mechanism.
    If the backend raises, they fall back to the
    :class:`~deepagents.backends.sandbox.BaseSandbox` base64-via-execute path.

    Args:
        backend: An active backend instance.
    """

    def __init__(self, *, backend: "KubernetesBackendProtocol") -> None:
        self._backend = backend

    # ------------------------------------------------------------------
    # BaseSandbox: id
    # ------------------------------------------------------------------

    @property
    def id(self) -> str:
        """Unique sandbox identifier delegated to the backend.

        Returns:
            Sandbox ID string (Sandbox CR name for agent-sandbox mode,
            short hex ID for raw mode).
        """
        return self._backend.id

    # ------------------------------------------------------------------
    # BaseSandbox: execute (sync)
    # ------------------------------------------------------------------

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Execute *command* inside the sandbox and return the result.

        Args:
            command: Shell command string.
            timeout: Per-call timeout in seconds.

        Returns:
            :class:`~deepagents.backends.protocol.ExecuteResponse`.
        """
        return self._backend.execute(command, timeout=timeout)

    # ------------------------------------------------------------------
    # BaseSandbox: execute (async)
    # ------------------------------------------------------------------

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Async variant of :meth:`execute`.

        Args:
            command: Shell command string.
            timeout: Per-call timeout in seconds.

        Returns:
            :class:`~deepagents.backends.protocol.ExecuteResponse`.
        """
        return await self._backend.aexecute(command, timeout=timeout)

    # ------------------------------------------------------------------
    # BaseSandbox: file transfer
    # ------------------------------------------------------------------

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Upload files, falling back to BaseSandbox base64-via-execute on error.

        Tries the backend's native upload mechanism first. If the backend raises
        an exception, falls back to
        :meth:`~deepagents.backends.sandbox.BaseSandbox.upload_files` which
        encodes each file as base64 and transfers it via :meth:`execute`.

        Args:
            files: List of ``(absolute_path, content_bytes)`` tuples.

        Returns:
            List of :class:`~deepagents.backends.protocol.FileUploadResponse`.
        """
        try:
            return self._backend.upload_files(files)
        except Exception as exc:
            logger.warning(
                "Backend upload failed (%s), falling back to base64-via-execute", exc
            )
            return super().upload_files(files)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download files, falling back to BaseSandbox base64-via-execute on error.

        Tries the backend's native download mechanism first. If the backend
        raises, falls back to
        :meth:`~deepagents.backends.sandbox.BaseSandbox.download_files`.

        Args:
            paths: Absolute paths to download from the sandbox.

        Returns:
            List of :class:`~deepagents.backends.protocol.FileDownloadResponse`.
        """
        try:
            return self._backend.download_files(paths)
        except Exception as exc:
            logger.warning(
                "Backend download failed (%s), falling back to base64-via-execute", exc
            )
            return super().download_files(paths)
