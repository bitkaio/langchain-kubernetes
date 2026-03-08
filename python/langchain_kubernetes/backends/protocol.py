"""KubernetesBackendProtocol — common interface for all backend implementations."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from deepagents.backends.protocol import ExecuteResponse, FileDownloadResponse, FileUploadResponse


@runtime_checkable
class KubernetesBackendProtocol(Protocol):
    """Common interface implemented by both ``AgentSandboxBackend`` and ``RawK8sBackend``.

    ``KubernetesSandbox`` holds a reference to one of these and delegates all
    operations to it. Neither the sandbox nor the provider care which concrete
    implementation is active.
    """

    @property
    def id(self) -> str:
        """Unique sandbox identifier."""
        ...

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Execute a shell command inside the sandbox.

        Args:
            command: Shell command string.
            timeout: Per-call timeout in seconds.

        Returns:
            :class:`~deepagents.backends.protocol.ExecuteResponse`.
        """
        ...

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Async variant of :meth:`execute`.

        Args:
            command: Shell command string.
            timeout: Per-call timeout in seconds.

        Returns:
            :class:`~deepagents.backends.protocol.ExecuteResponse`.
        """
        ...

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Upload files to the sandbox.

        Args:
            files: List of ``(absolute_path, content_bytes)`` tuples.

        Returns:
            List of :class:`~deepagents.backends.protocol.FileUploadResponse`.

        Raises:
            Exception: On transport or backend failure. The caller
                (:class:`~langchain_kubernetes.sandbox.KubernetesSandbox`) catches
                this and falls back to BaseSandbox base64-via-execute.
        """
        ...

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download files from the sandbox.

        Args:
            paths: Absolute file paths to download.

        Returns:
            List of :class:`~deepagents.backends.protocol.FileDownloadResponse`.

        Raises:
            Exception: On transport or backend failure.
        """
        ...

    def cleanup(self) -> None:
        """Release all resources held by this backend instance.

        Idempotent — calling more than once must not raise.
        """
        ...
