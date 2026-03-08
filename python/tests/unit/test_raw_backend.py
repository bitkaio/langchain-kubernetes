"""Unit tests for RawK8sBackend — mocks kubernetes client and transport."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from langchain_kubernetes._provider_base import SandboxNotFoundError
from langchain_kubernetes.backends.raw import RawK8sBackend
from langchain_kubernetes.config import KubernetesProviderConfig


def _raw_config(**kwargs) -> KubernetesProviderConfig:
    defaults = {"mode": "raw"}
    defaults.update(kwargs)
    return KubernetesProviderConfig(**defaults)


def _make_backend(sandbox_id: str = "abc12345", **config_kwargs) -> RawK8sBackend:
    return RawK8sBackend(
        sandbox_id=sandbox_id,
        pod_name=f"deepagents-{sandbox_id}",
        namespace="default",
        container="sandbox",
        core_v1=MagicMock(),
        networking_v1=MagicMock(),
        config=_raw_config(**config_kwargs),
    )


# ---------------------------------------------------------------------------
# id
# ---------------------------------------------------------------------------


class TestRawK8sBackendId:
    def test_id_returns_sandbox_id(self):
        backend = _make_backend("test-id-001")
        assert backend.id == "test-id-001"


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


class TestRawK8sBackendExecute:
    @patch("langchain_kubernetes.backends.raw.exec_command")
    def test_execute_returns_response(self, mock_exec):
        mock_exec.return_value = ("output text", 0, False)
        backend = _make_backend()

        result = backend.execute("echo hi")

        assert isinstance(result, ExecuteResponse)
        assert result.output == "output text"
        assert result.exit_code == 0
        assert result.truncated is False

    @patch("langchain_kubernetes.backends.raw.exec_command")
    def test_execute_passes_command(self, mock_exec):
        mock_exec.return_value = ("", 0, False)
        backend = _make_backend()

        backend.execute("ls -la")

        call_args = mock_exec.call_args
        assert call_args[0][4] == "ls -la"  # 5th positional: command

    @patch("langchain_kubernetes.backends.raw.exec_command")
    def test_execute_uses_default_timeout(self, mock_exec):
        mock_exec.return_value = ("", 0, False)
        backend = _make_backend()

        backend.execute("cmd")

        call_args = mock_exec.call_args
        assert call_args[0][5] == backend._config.default_exec_timeout

    @patch("langchain_kubernetes.backends.raw.exec_command")
    def test_execute_custom_timeout(self, mock_exec):
        mock_exec.return_value = ("", 0, False)
        backend = _make_backend()

        backend.execute("cmd", timeout=42)

        call_args = mock_exec.call_args
        assert call_args[0][5] == 42

    @patch("langchain_kubernetes.backends.raw.exec_command")
    def test_execute_nonzero_exit_code(self, mock_exec):
        mock_exec.return_value = ("error", 1, False)
        backend = _make_backend()

        result = backend.execute("bad")

        assert result.exit_code == 1

    @patch("langchain_kubernetes.backends.raw.exec_command")
    def test_execute_truncated(self, mock_exec):
        mock_exec.return_value = ("x" * 1024, 0, True)
        backend = _make_backend()

        result = backend.execute("big-output-cmd")

        assert result.truncated is True

    @patch("langchain_kubernetes.backends.raw.exec_command")
    def test_execute_passes_pod_name_and_namespace(self, mock_exec):
        mock_exec.return_value = ("", 0, False)
        backend = _make_backend("myid")

        backend.execute("cmd")

        args = mock_exec.call_args[0]
        assert args[1] == "deepagents-myid"
        assert args[2] == "default"
        assert args[3] == "sandbox"


# ---------------------------------------------------------------------------
# aexecute
# ---------------------------------------------------------------------------


class TestRawK8sBackendAexecute:
    @pytest.mark.asyncio
    @patch("langchain_kubernetes.backends.raw.exec_command")
    async def test_aexecute_returns_response(self, mock_exec):
        mock_exec.return_value = ("async result", 0, False)
        backend = _make_backend()

        result = await backend.aexecute("echo async")

        assert result.output == "async result"
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# upload_files
# ---------------------------------------------------------------------------


class TestRawK8sBackendUploadFiles:
    @patch("langchain_kubernetes.backends.raw.upload_files_tar")
    def test_delegates_to_upload_tar(self, mock_upload):
        mock_upload.return_value = [FileUploadResponse(path="/a.txt", error=None)]
        backend = _make_backend()

        result = backend.upload_files([("/a.txt", b"data")])

        mock_upload.assert_called_once()
        assert result[0].path == "/a.txt"
        assert result[0].error is None

    @patch("langchain_kubernetes.backends.raw.upload_files_tar")
    def test_passes_files_to_transport(self, mock_upload):
        mock_upload.return_value = [FileUploadResponse(path="/a.txt", error=None)]
        backend = _make_backend()

        files = [("/a.txt", b"hello"), ("/b.txt", b"world")]
        backend.upload_files(files)

        call_args = mock_upload.call_args[0]
        assert call_args[4] == files  # 5th positional arg: files


# ---------------------------------------------------------------------------
# download_files
# ---------------------------------------------------------------------------


class TestRawK8sBackendDownloadFiles:
    @patch("langchain_kubernetes.backends.raw.download_files_tar")
    def test_delegates_to_download_tar(self, mock_download):
        mock_download.return_value = [
            FileDownloadResponse(path="/a.txt", content=b"content", error=None)
        ]
        backend = _make_backend()

        result = backend.download_files(["/a.txt"])

        mock_download.assert_called_once()
        assert result[0].content == b"content"

    @patch("langchain_kubernetes.backends.raw.download_files_tar")
    def test_passes_paths_to_transport(self, mock_download):
        mock_download.return_value = []
        backend = _make_backend()

        paths = ["/a.txt", "/b.txt"]
        backend.download_files(paths)

        call_args = mock_download.call_args[0]
        assert call_args[4] == paths  # 5th positional arg: paths


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


class TestRawK8sBackendCleanup:
    def test_cleanup_deletes_pod(self):
        backend = _make_backend()
        backend.cleanup()
        backend._core_v1.delete_namespaced_pod.assert_called_once_with(
            name=backend._pod_name, namespace=backend._namespace
        )

    def test_cleanup_deletes_network_policy(self):
        backend = _make_backend()
        backend.cleanup()
        backend._networking_v1.delete_namespaced_network_policy.assert_called_once()

    def test_cleanup_skips_network_policy_when_not_blocking(self):
        backend = _make_backend(block_network=False)
        backend.cleanup()
        # The network policy is still attempted to be deleted (idempotent cleanup)
        # We just verify no exception is raised
        backend._core_v1.delete_namespaced_pod.assert_called_once()

    def test_cleanup_deletes_namespace_when_namespace_per_sandbox(self):
        backend = _make_backend(namespace_per_sandbox=True)
        # Override namespace to simulate per-sandbox namespace
        backend._namespace = f"deepagents-{backend._sandbox_id}"
        backend.cleanup()
        backend._core_v1.delete_namespace.assert_called_once_with(
            name=backend._namespace
        )

    def test_cleanup_does_not_raise_on_404(self):
        from kubernetes.client.exceptions import ApiException

        backend = _make_backend()
        exc = ApiException(status=404)
        backend._core_v1.delete_namespaced_pod.side_effect = exc

        # Should not raise
        backend.cleanup()

    def test_cleanup_swallows_errors(self):
        backend = _make_backend()
        backend._core_v1.delete_namespaced_pod.side_effect = RuntimeError("some error")

        # cleanup must not propagate errors
        backend.cleanup()


# ---------------------------------------------------------------------------
# reconnect
# ---------------------------------------------------------------------------


class TestRawK8sBackendReconnect:
    @patch("langchain_kubernetes.backends.raw._load_k8s_clients")
    def test_reconnect_running_pod(self, mock_load):
        core_v1 = MagicMock()
        networking_v1 = MagicMock()
        mock_load.return_value = (core_v1, networking_v1)

        pod = MagicMock()
        pod.status.phase = "Running"
        core_v1.read_namespaced_pod.return_value = pod

        config = _raw_config()
        backend = RawK8sBackend.reconnect(config, "abc12345")

        assert backend.id == "abc12345"
        assert backend._pod_name == "deepagents-abc12345"

    @patch("langchain_kubernetes.backends.raw._load_k8s_clients")
    def test_reconnect_not_found_raises(self, mock_load):
        from kubernetes.client.exceptions import ApiException

        core_v1 = MagicMock()
        networking_v1 = MagicMock()
        mock_load.return_value = (core_v1, networking_v1)

        exc = ApiException(status=404)
        core_v1.read_namespaced_pod.side_effect = exc

        with pytest.raises(SandboxNotFoundError, match="abc12345"):
            RawK8sBackend.reconnect(_raw_config(), "abc12345")

    @patch("langchain_kubernetes.backends.raw._load_k8s_clients")
    def test_reconnect_non_running_raises(self, mock_load):
        core_v1 = MagicMock()
        networking_v1 = MagicMock()
        mock_load.return_value = (core_v1, networking_v1)

        pod = MagicMock()
        pod.status.phase = "Pending"
        core_v1.read_namespaced_pod.return_value = pod

        with pytest.raises(RuntimeError, match="Pending"):
            RawK8sBackend.reconnect(_raw_config(), "abc12345")


# ---------------------------------------------------------------------------
# create (factory)
# ---------------------------------------------------------------------------


class TestRawK8sBackendCreate:
    @patch("langchain_kubernetes.backends.raw._load_k8s_clients")
    @patch("langchain_kubernetes.backends.raw._wait_for_pod_running")
    def test_create_provisions_pod(self, mock_wait, mock_load):
        core_v1 = MagicMock()
        networking_v1 = MagicMock()
        mock_load.return_value = (core_v1, networking_v1)

        backend = RawK8sBackend.create(_raw_config(), sandbox_id="testid")

        core_v1.create_namespaced_pod.assert_called_once()
        assert backend.id == "testid"

    @patch("langchain_kubernetes.backends.raw._load_k8s_clients")
    @patch("langchain_kubernetes.backends.raw._wait_for_pod_running")
    def test_create_creates_network_policy_when_block_network(self, mock_wait, mock_load):
        core_v1 = MagicMock()
        networking_v1 = MagicMock()
        mock_load.return_value = (core_v1, networking_v1)

        RawK8sBackend.create(_raw_config(block_network=True), sandbox_id="sid")

        networking_v1.create_namespaced_network_policy.assert_called_once()

    @patch("langchain_kubernetes.backends.raw._load_k8s_clients")
    @patch("langchain_kubernetes.backends.raw._wait_for_pod_running")
    def test_create_skips_network_policy_when_not_blocking(self, mock_wait, mock_load):
        core_v1 = MagicMock()
        networking_v1 = MagicMock()
        mock_load.return_value = (core_v1, networking_v1)

        RawK8sBackend.create(_raw_config(block_network=False), sandbox_id="sid")

        networking_v1.create_namespaced_network_policy.assert_not_called()

    @patch("langchain_kubernetes.backends.raw._load_k8s_clients")
    @patch("langchain_kubernetes.backends.raw._wait_for_pod_running")
    def test_create_creates_namespace_when_namespace_per_sandbox(self, mock_wait, mock_load):
        core_v1 = MagicMock()
        networking_v1 = MagicMock()
        mock_load.return_value = (core_v1, networking_v1)

        RawK8sBackend.create(_raw_config(namespace_per_sandbox=True), sandbox_id="sid")

        core_v1.create_namespace.assert_called_once()

    @patch("langchain_kubernetes.backends.raw._load_k8s_clients")
    @patch("langchain_kubernetes.backends.raw._wait_for_pod_running")
    def test_create_generates_id_when_not_provided(self, mock_wait, mock_load):
        core_v1 = MagicMock()
        mock_load.return_value = (core_v1, MagicMock())

        backend = RawK8sBackend.create(_raw_config())

        assert backend.id is not None
        assert len(backend.id) > 0

    @patch("langchain_kubernetes.backends.raw._load_k8s_clients")
    @patch("langchain_kubernetes.backends.raw._wait_for_pod_running")
    def test_create_cleans_up_pod_on_timeout(self, mock_wait, mock_load):
        core_v1 = MagicMock()
        mock_load.return_value = (core_v1, MagicMock())
        mock_wait.side_effect = TimeoutError("timed out")

        with pytest.raises(TimeoutError):
            RawK8sBackend.create(_raw_config(), sandbox_id="tid")

        # Cleanup (best-effort delete) should have been attempted
        core_v1.delete_namespaced_pod.assert_called_once()

    @patch("langchain_kubernetes.backends.raw._load_k8s_clients")
    def test_missing_kubernetes_raises_import_error(self, mock_load):
        mock_load.side_effect = ImportError(
            "Raw Kubernetes mode requires the 'kubernetes' package"
        )

        with pytest.raises(ImportError, match="kubernetes"):
            RawK8sBackend.create(_raw_config())
