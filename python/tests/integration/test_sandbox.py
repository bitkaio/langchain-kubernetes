"""Integration tests for KubernetesSandbox (require a live kind cluster)."""

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

    def test_execute_exit_code_nonzero(self, provider):
        sandbox = provider.get_or_create()
        try:
            result = sandbox.execute("exit 42", timeout=10)
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

    def test_execute_timeout_returns_minus_one(self, provider):
        sandbox = provider.get_or_create()
        try:
            result = sandbox.execute("sleep 60", timeout=2)
            assert result.exit_code == -1
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
    def test_write_and_read_file(self, provider):
        sandbox = provider.get_or_create()
        try:
            sandbox.write("/tmp/test.txt", "hello from test\n")
            content = sandbox.read("/tmp/test.txt")
            assert "hello from test" in content
        finally:
            provider.delete(sandbox_id=sandbox.id)

    def test_upload_and_download_file(self, provider):
        sandbox = provider.get_or_create()
        try:
            data = b"binary content \x00\x01\x02"
            upload_resp = sandbox.upload_files([("/tmp/binary.bin", data)])
            assert upload_resp[0].error is None

            download_resp = sandbox.download_files(["/tmp/binary.bin"])
            assert download_resp[0].content == data
        finally:
            provider.delete(sandbox_id=sandbox.id)

    def test_ls_info(self, provider):
        sandbox = provider.get_or_create()
        try:
            sandbox.execute("mkdir -p /tmp/testdir && touch /tmp/testdir/a.txt")
            entries = sandbox.ls_info("/tmp/testdir")
            paths = [e["path"] for e in entries]
            assert any("a.txt" in p for p in paths)
        finally:
            provider.delete(sandbox_id=sandbox.id)

    def test_glob_info(self, provider):
        sandbox = provider.get_or_create()
        try:
            sandbox.execute("mkdir -p /tmp/glob && touch /tmp/glob/x.py /tmp/glob/y.py")
            results = sandbox.glob_info("*.py", path="/tmp/glob")
            assert len(results) >= 2
        finally:
            provider.delete(sandbox_id=sandbox.id)

    def test_grep_raw(self, provider):
        sandbox = provider.get_or_create()
        try:
            sandbox.write("/tmp/search.txt", "foo bar baz\nqux quux\n")
            matches = sandbox.grep_raw("foo", path="/tmp/search.txt")
            assert isinstance(matches, list)
            assert len(matches) >= 1
            assert "foo" in matches[0]["text"]
        finally:
            provider.delete(sandbox_id=sandbox.id)

    def test_edit_file(self, provider):
        sandbox = provider.get_or_create()
        try:
            sandbox.write("/tmp/edit.txt", "hello world\n")
            result = sandbox.edit("/tmp/edit.txt", "hello", "goodbye")
            assert result.error is None
            content = sandbox.read("/tmp/edit.txt")
            assert "goodbye" in content
        finally:
            provider.delete(sandbox_id=sandbox.id)


@pytest.mark.integration
class TestKubernetesSandboxReconnect:
    def test_reconnect_to_running_sandbox(self, provider):
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
            provider.get_or_create(sandbox_id="deepagents-sandbox-deadbeef")
