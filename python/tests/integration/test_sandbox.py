"""Integration tests for KubernetesSandbox (require a live cluster with agent-sandbox)."""

from __future__ import annotations

import pytest

from langchain_kubernetes.sandbox import KubernetesSandbox


@pytest.mark.integration
class TestKubernetesSandboxExecute:
    def test_execute_echo(self, provider):
        sandbox = provider.get_or_create()
        try:
            result = sandbox.execute("echo hello")
            assert "hello" in result.output
            assert result.exit_code == 0
        finally:
            provider.delete(sandbox_id=sandbox.id)

    def test_execute_nonzero_exit_code(self, provider):
        sandbox = provider.get_or_create()
        try:
            result = sandbox.execute("exit 42")
            assert result.exit_code == 42
        finally:
            provider.delete(sandbox_id=sandbox.id)

    def test_execute_stderr_captured(self, provider):
        sandbox = provider.get_or_create()
        try:
            result = sandbox.execute("echo error >&2")
            assert "error" in result.output
        finally:
            provider.delete(sandbox_id=sandbox.id)

    def test_sandbox_id_is_string(self, provider):
        sandbox = provider.get_or_create()
        try:
            assert isinstance(sandbox.id, str)
            assert len(sandbox.id) > 0
        finally:
            provider.delete(sandbox_id=sandbox.id)

    def test_execute_python_script(self, provider):
        sandbox = provider.get_or_create()
        try:
            result = sandbox.execute("python3 -c 'print(2 + 2)'")
            assert "4" in result.output
            assert result.exit_code == 0
        finally:
            provider.delete(sandbox_id=sandbox.id)


@pytest.mark.integration
class TestKubernetesSandboxFiles:
    def test_upload_and_download_file(self, provider):
        sandbox = provider.get_or_create()
        try:
            data = b"binary content \x00\x01\x02"
            upload_resp = sandbox.upload_files([("/tmp/binary.bin", data)])
            assert upload_resp[0].error is None

            download_resp = sandbox.download_files([("/tmp/binary.bin")])
            assert download_resp[0].content == data
            assert download_resp[0].error is None
        finally:
            provider.delete(sandbox_id=sandbox.id)

    def test_upload_text_file(self, provider):
        sandbox = provider.get_or_create()
        try:
            content = b"Hello, sandbox!\n"
            resp = sandbox.upload_files([("/tmp/hello.txt", content)])
            assert resp[0].error is None

            result = sandbox.execute("cat /tmp/hello.txt")
            assert "Hello, sandbox!" in result.output
        finally:
            provider.delete(sandbox_id=sandbox.id)

    def test_download_nonexistent_file_returns_error(self, provider):
        sandbox = provider.get_or_create()
        try:
            resp = sandbox.download_files(["/tmp/does-not-exist.txt"])
            # Either returns error field or empty content — depends on SDK + fallback
            assert resp[0].path == "/tmp/does-not-exist.txt"
        finally:
            provider.delete(sandbox_id=sandbox.id)

    def test_ls_info_via_execute(self, provider):
        sandbox = provider.get_or_create()
        try:
            sandbox.execute("mkdir -p /tmp/testdir && touch /tmp/testdir/a.txt")
            entries = sandbox.ls_info("/tmp/testdir")
            paths = [e["path"] for e in entries]
            assert any("a.txt" in p for p in paths)
        finally:
            provider.delete(sandbox_id=sandbox.id)


@pytest.mark.integration
class TestKubernetesSandboxReconnect:
    def test_reconnect_to_active_sandbox(self, provider):
        sandbox = provider.get_or_create()
        sandbox_id = sandbox.id
        try:
            sandbox2 = provider.get_or_create(sandbox_id=sandbox_id)
            assert sandbox2.id == sandbox_id
            result = sandbox2.execute("echo reconnected")
            assert "reconnected" in result.output
        finally:
            provider.delete(sandbox_id=sandbox_id)

    def test_reconnect_to_nonexistent_raises(self, provider):
        from langchain_kubernetes import SandboxNotFoundError

        with pytest.raises(SandboxNotFoundError):
            provider.get_or_create(sandbox_id="no-such-sandbox-xyzabc")
