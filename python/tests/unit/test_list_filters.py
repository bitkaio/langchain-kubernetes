"""Unit tests for provider.list() with filters and pagination."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from langchain_kubernetes._labels import LABEL_THREAD_ID
from langchain_kubernetes._types import SandboxInfo, SandboxListResponse
from langchain_kubernetes.config import KubernetesProviderConfig
from langchain_kubernetes.provider import KubernetesProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _raw_config(**kwargs) -> KubernetesProviderConfig:
    defaults = {"mode": "raw"}
    defaults.update(kwargs)
    return KubernetesProviderConfig(**defaults)


def _make_sandbox_info(sid: str, *, status: str = "running", thread_id: str | None = None) -> SandboxInfo:
    labels = {}
    if thread_id:
        labels[LABEL_THREAD_ID] = thread_id
    return SandboxInfo(
        id=sid,
        namespace="default",
        thread_id=thread_id,
        labels=labels,
        status=status,
    )


# ---------------------------------------------------------------------------
# List with filters
# ---------------------------------------------------------------------------


class TestListWithFilters:
    def test_list_returns_all_sandboxes(self):
        provider = KubernetesProvider(_raw_config())
        all_sandboxes = [
            _make_sandbox_info("sb-1"),
            _make_sandbox_info("sb-2"),
        ]

        with patch.object(
            provider, "_list_raw", return_value=SandboxListResponse(sandboxes=all_sandboxes)
        ):
            result = provider.list()

        assert len(result.sandboxes) == 2

    def test_list_filters_by_thread_id(self):
        """list(thread_id=...) filters by that thread."""
        provider = KubernetesProvider(_raw_config())

        # _list_raw handles the label_selector — just verify thread_id is passed
        with patch.object(
            provider, "_list_raw", return_value=SandboxListResponse(sandboxes=[])
        ) as mock_list:
            provider.list(thread_id="my-thread")
            call_kwargs = mock_list.call_args[1]
            assert call_kwargs["thread_id"] == "my-thread"

    def test_list_filters_by_status(self):
        """list(status='running') keeps only running sandboxes."""
        provider = KubernetesProvider(_raw_config())

        # Status filtering happens in _list_raw
        with patch.object(
            provider, "_list_raw", return_value=SandboxListResponse(sandboxes=[])
        ) as mock_list:
            provider.list(status="running")
            call_kwargs = mock_list.call_args[1]
            assert call_kwargs["status"] == "running"

    def test_list_passes_cursor(self):
        """Pagination cursor is forwarded."""
        provider = KubernetesProvider(_raw_config())

        with patch.object(
            provider, "_list_raw", return_value=SandboxListResponse(sandboxes=[])
        ) as mock_list:
            provider.list(cursor="some-continue-token")
            call_args = mock_list.call_args
            # cursor may be passed as positional or keyword arg
            all_args = list(call_args.args) + list(call_args.kwargs.values())
            assert "some-continue-token" in all_args

    def test_list_pagination_cursor_in_response(self):
        """SandboxListResponse carries next cursor when more pages exist."""
        provider = KubernetesProvider(_raw_config())

        with patch.object(
            provider,
            "_list_raw",
            return_value=SandboxListResponse(
                sandboxes=[_make_sandbox_info("sb-1")],
                cursor="next-page-token",
            ),
        ):
            result = provider.list()

        assert result.cursor == "next-page-token"

    def test_list_raw_uses_k8s_label_selector(self):
        """_list_raw queries the k8s API with the managed-by selector."""
        provider = KubernetesProvider(_raw_config())

        mock_pod = MagicMock()
        mock_pod.metadata.name = "deepagents-abc1"
        mock_pod.metadata.namespace = "default"
        mock_pod.metadata.labels = {"deepagents.langchain.com/sandbox-id": "abc1"}
        mock_pod.metadata.annotations = {}
        mock_pod.status.phase = "Running"

        mock_pod_list = MagicMock()
        mock_pod_list.items = [mock_pod]
        mock_pod_list.metadata._continue = None

        mock_core_v1 = MagicMock()
        mock_core_v1.list_namespaced_pod.return_value = mock_pod_list

        with patch(
            "langchain_kubernetes.backends.raw.RawK8sBackend.load_k8s_clients",
            return_value=(mock_core_v1, MagicMock()),
        ):
            result = provider._list_raw()

        mock_core_v1.list_namespaced_pod.assert_called_once()
        call_kwargs = mock_core_v1.list_namespaced_pod.call_args[1]
        assert "langchain-kubernetes.bitkaio.com/managed-by" in call_kwargs["label_selector"]
        assert len(result.sandboxes) == 1
