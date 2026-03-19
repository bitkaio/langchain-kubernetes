"""AgentSandboxBackend — wraps the k8s-agent-sandbox SandboxClient."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Callable

from deepagents.backends.protocol import ExecuteResponse, FileDownloadResponse, FileUploadResponse

from langchain_kubernetes._utils import map_execution_result

if TYPE_CHECKING:
    from k8s_agent_sandbox import SandboxClient

logger = logging.getLogger(__name__)

# A callable invoked after each execute() to track last-activity.
# Signature: () -> None, fire-and-forget style.
ActivityCallback = Callable[[], None]


class AgentSandboxBackend:
    """Backend implementation backed by the ``kubernetes-sigs/agent-sandbox`` SDK.

    The ``SandboxClient`` context must already be entered (sandbox provisioned
    and ready) before this object is constructed. Use
    :class:`~langchain_kubernetes.provider.KubernetesProvider` to manage the
    full lifecycle.

    Args:
        client: An *entered* ``SandboxClient`` instance.
        sandbox_name: The Kubernetes Sandbox CR name (``client.sandbox_name``).
    """

    def __init__(
        self,
        *,
        client: "SandboxClient",
        sandbox_name: str,
        activity_callback: ActivityCallback | None = None,
    ) -> None:
        self._client = client
        self._sandbox_name = sandbox_name
        self._activity_callback = activity_callback

    # ------------------------------------------------------------------
    # Protocol: id
    # ------------------------------------------------------------------

    @property
    def id(self) -> str:
        """The Sandbox CR name used as the unique sandbox identifier.

        Returns:
            Sandbox CR name, e.g. ``"my-template-a1b2c3d4"``.
        """
        return self._sandbox_name

    # ------------------------------------------------------------------
    # Protocol: execute
    # ------------------------------------------------------------------

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Run *command* via ``SandboxClient.run()``.

        Args:
            command: Shell command string.
            timeout: Per-call timeout in seconds. Defaults to 30 minutes.

        Returns:
            :class:`~deepagents.backends.protocol.ExecuteResponse`.
        """
        effective_timeout = timeout if timeout is not None else 60 * 30
        logger.debug("exec [%s] %s", self._sandbox_name, command[:120])
        result = self._client.run(command, timeout=effective_timeout)
        if self._activity_callback is not None:
            try:
                self._activity_callback()
            except Exception as exc:
                logger.warning("last-activity callback failed for %s: %s", self._sandbox_name, exc)
        return map_execution_result(result)

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
        """Upload files via ``SandboxClient.write()``.

        Args:
            files: List of ``(absolute_path, content_bytes)`` tuples.

        Returns:
            List of :class:`~deepagents.backends.protocol.FileUploadResponse`.

        Raises:
            Exception: If any individual write fails (propagated to the sandbox
                layer which can fall back to base64-via-execute).
        """
        responses: list[FileUploadResponse] = []
        for path, content in files:
            self._client.write(path, content)
            responses.append(FileUploadResponse(path=path, error=None))
            logger.debug("uploaded %s to sandbox %s", path, self._sandbox_name)
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download files via ``SandboxClient.read()``.

        Args:
            paths: Absolute paths to download from the sandbox.

        Returns:
            List of :class:`~deepagents.backends.protocol.FileDownloadResponse`.

        Raises:
            Exception: If any individual read fails.
        """
        responses: list[FileDownloadResponse] = []
        for path in paths:
            content: bytes = self._client.read(path)
            responses.append(FileDownloadResponse(path=path, content=content, error=None))
            logger.debug("downloaded %s from sandbox %s", path, self._sandbox_name)
        return responses

    # ------------------------------------------------------------------
    # Protocol: cleanup
    # ------------------------------------------------------------------

    def cleanup(self) -> None:
        """Exit the ``SandboxClient`` context to clean up the Sandbox CR.

        Triggers controller-side cleanup (Pod deletion, warm-pool return, etc.).
        Idempotent — errors during cleanup are logged but not re-raised.
        """
        logger.debug("cleaning up agent-sandbox backend %s", self._sandbox_name)
        try:
            self._client.__exit__(None, None, None)
        except Exception as exc:
            logger.warning("Error during SandboxClient cleanup for %s: %s", self._sandbox_name, exc)
