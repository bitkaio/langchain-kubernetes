"""Unit tests for KubernetesSandbox — mocks SandboxClient."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from deepagents.backends.protocol import FileDownloadResponse, FileUploadResponse
from langchain_kubernetes.sandbox import KubernetesSandbox


def _make_execution_result(stdout: str = "", stderr: str = "", exit_code: int = 0):
    result = MagicMock()
    result.stdout = stdout
    result.stderr = stderr
    result.exit_code = exit_code
    return result


def _make_sandbox(sandbox_name: str = "test-sandbox-abc123"):
    client = MagicMock()
    client.sandbox_name = sandbox_name
    return KubernetesSandbox(client=client, sandbox_name=sandbox_name), client


class TestKubernetesSandboxId:
    def test_id_returns_sandbox_name(self):
        sb, _ = _make_sandbox("my-sandbox-001")
        assert sb.id == "my-sandbox-001"


class TestKubernetesSandboxExecute:
    def test_execute_calls_client_run(self):
        sb, client = _make_sandbox()
        client.run.return_value = _make_execution_result(stdout="hello\n", exit_code=0)

        result = sb.execute("echo hello")

        client.run.assert_called_once_with("echo hello", timeout=60 * 30)
        assert result.output == "hello\n"
        assert result.exit_code == 0
        assert result.truncated is False

    def test_execute_combines_stdout_and_stderr(self):
        sb, client = _make_sandbox()
        client.run.return_value = _make_execution_result(
            stdout="out\n", stderr="err\n", exit_code=1
        )

        result = sb.execute("cmd")

        assert result.output == "out\nerr\n"
        assert result.exit_code == 1

    def test_execute_stdout_only(self):
        sb, client = _make_sandbox()
        client.run.return_value = _make_execution_result(stdout="only out", stderr="", exit_code=0)

        result = sb.execute("cmd")
        assert result.output == "only out"

    def test_execute_stderr_only(self):
        sb, client = _make_sandbox()
        client.run.return_value = _make_execution_result(stdout="", stderr="error msg", exit_code=1)

        result = sb.execute("cmd")
        assert result.output == "error msg"

    def test_execute_custom_timeout(self):
        sb, client = _make_sandbox()
        client.run.return_value = _make_execution_result()

        sb.execute("cmd", timeout=5)

        client.run.assert_called_once_with("cmd", timeout=5)

    def test_execute_nonzero_exit_code(self):
        sb, client = _make_sandbox()
        client.run.return_value = _make_execution_result(exit_code=127)

        result = sb.execute("bad-cmd")
        assert result.exit_code == 127


class TestKubernetesSandboxUploadFiles:
    def test_upload_success(self):
        sb, client = _make_sandbox()
        client.write.return_value = None

        responses = sb.upload_files([("/tmp/a.txt", b"hello")])

        client.write.assert_called_once_with("/tmp/a.txt", b"hello")
        assert len(responses) == 1
        assert responses[0].path == "/tmp/a.txt"
        assert responses[0].error is None

    def test_upload_multiple_files(self):
        sb, client = _make_sandbox()
        client.write.return_value = None

        files = [("/tmp/a.txt", b"a"), ("/tmp/b.txt", b"b")]
        responses = sb.upload_files(files)

        assert client.write.call_count == 2
        assert all(r.error is None for r in responses)

    def test_upload_falls_back_on_error(self):
        sb, client = _make_sandbox()
        client.write.side_effect = RuntimeError("write failed")

        # Patch BaseSandbox.upload_files to verify the fallback is triggered
        with patch(
            "deepagents.backends.sandbox.BaseSandbox.upload_files",
            return_value=[FileUploadResponse(path="/tmp/a.txt", error=None)],
        ) as mock_fallback:
            responses = sb.upload_files([("/tmp/a.txt", b"data")])

        client.write.assert_called_once()
        mock_fallback.assert_called_once()
        assert len(responses) == 1


class TestKubernetesSandboxDownloadFiles:
    def test_download_success(self):
        sb, client = _make_sandbox()
        client.read.return_value = b"file content"

        responses = sb.download_files(["/tmp/a.txt"])

        client.read.assert_called_once_with("/tmp/a.txt")
        assert len(responses) == 1
        assert responses[0].path == "/tmp/a.txt"
        assert responses[0].content == b"file content"
        assert responses[0].error is None

    def test_download_multiple_files(self):
        sb, client = _make_sandbox()
        client.read.side_effect = [b"content-a", b"content-b"]

        responses = sb.download_files(["/tmp/a.txt", "/tmp/b.txt"])

        assert len(responses) == 2
        assert responses[0].content == b"content-a"
        assert responses[1].content == b"content-b"

    def test_download_falls_back_on_error(self):
        sb, client = _make_sandbox()
        client.read.side_effect = RuntimeError("read failed")

        with patch(
            "deepagents.backends.sandbox.BaseSandbox.download_files",
            return_value=[FileDownloadResponse(path="/tmp/a.txt", content=None, error="file_not_found")],
        ) as mock_fallback:
            responses = sb.download_files(["/tmp/a.txt"])

        client.read.assert_called_once()
        mock_fallback.assert_called_once()
        assert len(responses) == 1


class TestKubernetesSandboxAexecute:
    @pytest.mark.asyncio
    async def test_aexecute_delegates_to_execute(self):
        sb, client = _make_sandbox()
        client.run.return_value = _make_execution_result(stdout="async result", exit_code=0)

        result = await sb.aexecute("echo async")

        assert result.output == "async result"
        assert result.exit_code == 0
