"""Unit tests for KubernetesSandbox — mocks the backend protocol."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from langchain_kubernetes.sandbox import KubernetesSandbox


def _make_execute_response(output: str = "", exit_code: int = 0, truncated: bool = False):
    return ExecuteResponse(output=output, exit_code=exit_code, truncated=truncated)


def _make_sandbox(sandbox_id: str = "test-sandbox-abc123"):
    """Return (KubernetesSandbox, mock_backend)."""
    backend = MagicMock()
    backend.id = sandbox_id
    backend.execute.return_value = _make_execute_response()
    backend.aexecute = AsyncMock(return_value=_make_execute_response())
    backend.upload_files.return_value = []
    backend.download_files.return_value = []
    return KubernetesSandbox(backend=backend), backend


# ---------------------------------------------------------------------------
# id
# ---------------------------------------------------------------------------


class TestKubernetesSandboxId:
    def test_id_delegates_to_backend(self):
        sb, _ = _make_sandbox("my-sandbox-001")
        assert sb.id == "my-sandbox-001"


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


class TestKubernetesSandboxExecute:
    def test_execute_delegates_to_backend(self):
        sb, backend = _make_sandbox()
        backend.execute.return_value = _make_execute_response(output="hello\n", exit_code=0)

        result = sb.execute("echo hello")

        backend.execute.assert_called_once_with("echo hello", timeout=None)
        assert result.output == "hello\n"
        assert result.exit_code == 0
        assert result.truncated is False

    def test_execute_passes_timeout(self):
        sb, backend = _make_sandbox()
        backend.execute.return_value = _make_execute_response()

        sb.execute("cmd", timeout=42)

        backend.execute.assert_called_once_with("cmd", timeout=42)

    def test_execute_nonzero_exit_code(self):
        sb, backend = _make_sandbox()
        backend.execute.return_value = _make_execute_response(exit_code=127)

        result = sb.execute("bad-cmd")
        assert result.exit_code == 127

    def test_execute_truncated(self):
        sb, backend = _make_sandbox()
        backend.execute.return_value = _make_execute_response(
            output="x" * 100, truncated=True
        )

        result = sb.execute("big-cmd")
        assert result.truncated is True


# ---------------------------------------------------------------------------
# aexecute
# ---------------------------------------------------------------------------


class TestKubernetesSandboxAexecute:
    @pytest.mark.asyncio
    async def test_aexecute_delegates_to_backend(self):
        sb, backend = _make_sandbox()
        backend.aexecute = AsyncMock(
            return_value=_make_execute_response(output="async result", exit_code=0)
        )

        result = await sb.aexecute("echo async")

        backend.aexecute.assert_awaited_once_with("echo async", timeout=None)
        assert result.output == "async result"

    @pytest.mark.asyncio
    async def test_aexecute_passes_timeout(self):
        sb, backend = _make_sandbox()
        backend.aexecute = AsyncMock(return_value=_make_execute_response())

        await sb.aexecute("cmd", timeout=99)

        backend.aexecute.assert_awaited_once_with("cmd", timeout=99)


# ---------------------------------------------------------------------------
# upload_files
# ---------------------------------------------------------------------------


class TestKubernetesSandboxUploadFiles:
    def test_upload_delegates_to_backend(self):
        sb, backend = _make_sandbox()
        backend.upload_files.return_value = [
            FileUploadResponse(path="/tmp/a.txt", error=None)
        ]

        result = sb.upload_files([("/tmp/a.txt", b"hello")])

        backend.upload_files.assert_called_once_with([("/tmp/a.txt", b"hello")])
        assert result[0].path == "/tmp/a.txt"
        assert result[0].error is None

    def test_upload_falls_back_on_backend_error(self):
        sb, backend = _make_sandbox()
        backend.upload_files.side_effect = RuntimeError("backend failed")

        with patch(
            "deepagents.backends.sandbox.BaseSandbox.upload_files",
            return_value=[FileUploadResponse(path="/tmp/a.txt", error=None)],
        ) as mock_fallback:
            result = sb.upload_files([("/tmp/a.txt", b"data")])

        backend.upload_files.assert_called_once()
        mock_fallback.assert_called_once()
        assert len(result) == 1

    def test_upload_multiple_files(self):
        sb, backend = _make_sandbox()
        files = [("/a.txt", b"a"), ("/b.txt", b"b")]
        backend.upload_files.return_value = [
            FileUploadResponse(path=p, error=None) for p, _ in files
        ]

        results = sb.upload_files(files)

        backend.upload_files.assert_called_once_with(files)
        assert len(results) == 2


# ---------------------------------------------------------------------------
# download_files
# ---------------------------------------------------------------------------


class TestKubernetesSandboxDownloadFiles:
    def test_download_delegates_to_backend(self):
        sb, backend = _make_sandbox()
        backend.download_files.return_value = [
            FileDownloadResponse(path="/tmp/a.txt", content=b"content", error=None)
        ]

        result = sb.download_files(["/tmp/a.txt"])

        backend.download_files.assert_called_once_with(["/tmp/a.txt"])
        assert result[0].content == b"content"
        assert result[0].error is None

    def test_download_falls_back_on_backend_error(self):
        sb, backend = _make_sandbox()
        backend.download_files.side_effect = RuntimeError("backend failed")

        with patch(
            "deepagents.backends.sandbox.BaseSandbox.download_files",
            return_value=[
                FileDownloadResponse(path="/tmp/a.txt", content=None, error="file_not_found")
            ],
        ) as mock_fallback:
            result = sb.download_files(["/tmp/a.txt"])

        backend.download_files.assert_called_once()
        mock_fallback.assert_called_once()
        assert len(result) == 1

    def test_download_multiple_files(self):
        sb, backend = _make_sandbox()
        backend.download_files.return_value = [
            FileDownloadResponse(path="/a.txt", content=b"a", error=None),
            FileDownloadResponse(path="/b.txt", content=b"b", error=None),
        ]

        results = sb.download_files(["/a.txt", "/b.txt"])

        assert len(results) == 2
        assert results[0].content == b"a"
        assert results[1].content == b"b"
