"""KubernetesSandbox: a BaseSandbox backed by kubernetes-sigs/agent-sandbox."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

from langchain_kubernetes._utils import map_execution_result

if TYPE_CHECKING:
    from k8s_agent_sandbox import SandboxClient

logger = logging.getLogger(__name__)


class KubernetesSandbox(BaseSandbox):
    """A DeepAgents sandbox backed by ``kubernetes-sigs/agent-sandbox``.

    The underlying ``SandboxClient`` context must already be entered (sandbox
    provisioned and ready) before this object is constructed. Use
    :class:`~langchain_kubernetes.provider.KubernetesProvider` to manage the
    full lifecycle.

    All filesystem helper methods (``read``, ``write``, ``edit``, ``ls_info``,
    ``glob_info``, ``grep_raw``) are inherited from
    :class:`~deepagents.backends.sandbox.BaseSandbox` — they work by
    constructing shell commands and calling :meth:`execute`.

    Args:
        client: An *entered* ``SandboxClient`` instance (context already active).
        sandbox_name: The Kubernetes Sandbox CR name (``client.sandbox_name``).
    """

    def __init__(self, *, client: "SandboxClient", sandbox_name: str) -> None:
        self._client = client
        self._sandbox_name = sandbox_name

    # ------------------------------------------------------------------
    # BaseSandbox: id
    # ------------------------------------------------------------------

    @property
    def id(self) -> str:
        """Unique sandbox identifier — the Kubernetes Sandbox CR name.

        Returns:
            The Sandbox CR name, e.g. ``"my-template-a1b2c3d4"``.
        """
        return self._sandbox_name

    # ------------------------------------------------------------------
    # BaseSandbox: execute (sync)
    # ------------------------------------------------------------------

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Execute *command* inside the sandbox and return the result.

        Args:
            command: Shell command string to run (executed by the sandbox runtime).
            timeout: Per-call timeout in seconds. Falls back to
                the provider's ``default_exec_timeout`` when ``None``.

        Returns:
            :class:`~deepagents.backends.protocol.ExecuteResponse` with combined
            stdout/stderr, the process exit code, and ``truncated=False``.
        """
        effective_timeout = timeout if timeout is not None else 60 * 30
        logger.debug("exec [%s] %s", self._sandbox_name, command[:120])
        result = self._client.run(command, timeout=effective_timeout)
        return map_execution_result(result)

    # ------------------------------------------------------------------
    # BaseSandbox: execute (async)
    # ------------------------------------------------------------------

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Async variant of :meth:`execute`.

        Runs the synchronous ``SandboxClient.run()`` in a thread pool so the
        event loop is not blocked.

        Args:
            command: Shell command string.
            timeout: Per-call timeout override in seconds.

        Returns:
            :class:`~deepagents.backends.protocol.ExecuteResponse`.
        """
        return await asyncio.to_thread(self.execute, command, timeout=timeout)

    # ------------------------------------------------------------------
    # BaseSandbox: file transfer
    # ------------------------------------------------------------------

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Upload files to the sandbox via ``SandboxClient.write()``.

        Falls back to the :class:`~deepagents.backends.sandbox.BaseSandbox`
        base64-via-execute path for individual files that fail.

        Args:
            files: List of ``(absolute_path, content_bytes)`` tuples.

        Returns:
            List of :class:`~deepagents.backends.protocol.FileUploadResponse`.
        """
        responses: list[FileUploadResponse] = []
        for path, content in files:
            try:
                self._client.write(path, content)
                responses.append(FileUploadResponse(path=path, error=None))
                logger.debug("uploaded %s to sandbox %s", path, self._sandbox_name)
            except Exception as exc:
                logger.warning("SDK write failed for %s (%s), falling back to execute", path, exc)
                # Fall back to BaseSandbox base64-via-execute for this file
                fallback = super().upload_files([(path, content)])
                responses.extend(fallback)
        return responses

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download files from the sandbox via ``SandboxClient.read()``.

        Falls back to the :class:`~deepagents.backends.sandbox.BaseSandbox`
        base64-via-execute path for individual files that fail.

        Args:
            paths: Absolute paths to download from the sandbox.

        Returns:
            List of :class:`~deepagents.backends.protocol.FileDownloadResponse`.
        """
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                content: bytes = self._client.read(path)
                responses.append(FileDownloadResponse(path=path, content=content, error=None))
                logger.debug("downloaded %s from sandbox %s", path, self._sandbox_name)
            except Exception as exc:
                logger.warning("SDK read failed for %s (%s), falling back to execute", path, exc)
                fallback = super().download_files([path])
                responses.extend(fallback)
        return responses
