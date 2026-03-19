"""Unit tests for thread_id support in get_or_create() and find_by_thread_id()."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from langchain_kubernetes._labels import (
    LABEL_MANAGED_BY,
    LABEL_MANAGED_BY_VALUE,
    LABEL_PREFIX,
    LABEL_THREAD_ID,
    sanitize_label_value,
)
from langchain_kubernetes.config import KubernetesProviderConfig
from langchain_kubernetes.provider import KubernetesProvider
from langchain_kubernetes.sandbox import KubernetesSandbox


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent_sandbox_config(**kwargs) -> KubernetesProviderConfig:
    defaults = {"mode": "agent-sandbox", "template_name": "test-template"}
    defaults.update(kwargs)
    return KubernetesProviderConfig(**defaults)


def _raw_config(**kwargs) -> KubernetesProviderConfig:
    defaults = {"mode": "raw"}
    defaults.update(kwargs)
    return KubernetesProviderConfig(**defaults)


def _make_mock_sandbox_client(sandbox_name: str = "test-sandbox-abc") -> MagicMock:
    client = MagicMock()
    client.sandbox_name = sandbox_name
    client.claim_name = sandbox_name
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    return client


def _make_mock_backend(sandbox_id: str = "test-backend-001") -> MagicMock:
    backend = MagicMock()
    backend.id = sandbox_id
    backend.cleanup = MagicMock()
    return backend


# ---------------------------------------------------------------------------
# Label sanitization
# ---------------------------------------------------------------------------


class TestSanitizeLabelValue:
    def test_valid_value_unchanged(self):
        safe, orig = sanitize_label_value("my-thread-123")
        assert safe == "my-thread-123"
        assert orig is None

    def test_value_with_invalid_chars_is_hashed(self):
        safe, orig = sanitize_label_value("thread@user/conv#1")
        assert len(safe) == 12
        assert safe == safe.lower()  # hex
        assert orig == "thread@user/conv#1"

    def test_value_too_long_is_hashed(self):
        long_val = "a" * 64
        safe, orig = sanitize_label_value(long_val)
        assert len(safe) == 12
        assert orig == long_val

    def test_exactly_63_chars_valid(self):
        val = "a" * 63
        safe, orig = sanitize_label_value(val)
        assert safe == val
        assert orig is None

    def test_single_char_valid(self):
        safe, orig = sanitize_label_value("a")
        assert safe == "a"
        assert orig is None

    def test_empty_string_valid(self):
        safe, orig = sanitize_label_value("")
        assert safe == ""
        assert orig is None


# ---------------------------------------------------------------------------
# Label merging in get_or_create
# ---------------------------------------------------------------------------


class TestLabelMerging:
    def test_managed_by_always_set(self):
        """Every sandbox must carry the managed-by label."""
        provider = KubernetesProvider(_agent_sandbox_config(
            default_labels={"env": "prod"}
        ))
        mock_client = _make_mock_sandbox_client("sb-labels-1")

        with patch("langchain_kubernetes.provider._build_agent_sandbox_client", return_value=mock_client):
            with patch.object(provider, "_patch_sandbox_claim") as mock_patch:
                provider.get_or_create()
                mock_patch.assert_called_once()
                args = mock_patch.call_args
                labels_passed = args[0][1]  # second positional arg
                assert labels_passed[LABEL_MANAGED_BY] == LABEL_MANAGED_BY_VALUE

    def test_default_labels_prefixed(self):
        provider = KubernetesProvider(_agent_sandbox_config(
            default_labels={"env": "test", "team": "ml"}
        ))
        mock_client = _make_mock_sandbox_client("sb-labels-2")

        with patch("langchain_kubernetes.provider._build_agent_sandbox_client", return_value=mock_client):
            with patch.object(provider, "_patch_sandbox_claim") as mock_patch:
                provider.get_or_create()
                labels = mock_patch.call_args[0][1]
                assert f"{LABEL_PREFIX}env" in labels
                assert labels[f"{LABEL_PREFIX}env"] == "test"
                assert labels[f"{LABEL_PREFIX}team"] == "ml"

    def test_call_labels_override_default_labels(self):
        provider = KubernetesProvider(_agent_sandbox_config(
            default_labels={"env": "prod"}
        ))
        mock_client = _make_mock_sandbox_client("sb-labels-3")

        with patch("langchain_kubernetes.provider._build_agent_sandbox_client", return_value=mock_client):
            with patch.object(provider, "_patch_sandbox_claim") as mock_patch:
                provider.get_or_create(labels={"env": "staging"})
                labels = mock_patch.call_args[0][1]
                assert labels[f"{LABEL_PREFIX}env"] == "staging"

    def test_thread_id_label_added(self):
        provider = KubernetesProvider(_agent_sandbox_config())
        mock_client = _make_mock_sandbox_client("sb-tid-1")

        with patch("langchain_kubernetes.provider._build_agent_sandbox_client", return_value=mock_client):
            with patch.object(provider, "_patch_sandbox_claim") as mock_patch:
                with patch.object(provider, "_find_by_thread_id_internal", return_value=None):
                    provider.get_or_create(thread_id="my-thread")
                    labels = mock_patch.call_args[0][1]
                    assert LABEL_THREAD_ID in labels
                    assert labels[LABEL_THREAD_ID] == "my-thread"

    def test_thread_id_sanitised_in_label(self):
        """Thread IDs with invalid chars are hashed."""
        provider = KubernetesProvider(_agent_sandbox_config())
        mock_client = _make_mock_sandbox_client("sb-tid-2")
        raw_thread_id = "thread@conv/123#special"
        expected_safe, _ = sanitize_label_value(raw_thread_id)

        with patch("langchain_kubernetes.provider._build_agent_sandbox_client", return_value=mock_client):
            with patch.object(provider, "_patch_sandbox_claim") as mock_patch:
                with patch.object(provider, "_find_by_thread_id_internal", return_value=None):
                    provider.get_or_create(thread_id=raw_thread_id)
                    labels = mock_patch.call_args[0][1]
                    assert labels[LABEL_THREAD_ID] == expected_safe


# ---------------------------------------------------------------------------
# Thread_id lookup hit/miss
# ---------------------------------------------------------------------------


class TestThreadIdLookup:
    def test_thread_id_lookup_hit_raw_returns_existing(self):
        """When a running Pod exists for thread_id, reconnect to it."""
        provider = KubernetesProvider(_raw_config())
        mock_backend = _make_mock_backend("existing-pod")
        mock_sandbox = KubernetesSandbox(backend=mock_backend)

        with patch.object(provider, "_find_by_thread_id_internal", return_value=mock_sandbox):
            result = provider.get_or_create(thread_id="abc-thread")

        assert result.id == "existing-pod"

    def test_thread_id_lookup_miss_creates_new(self):
        """When no Pod found for thread_id, create a new one."""
        provider = KubernetesProvider(_raw_config())
        mock_backend = _make_mock_backend("new-pod-001")

        with patch.object(provider, "_find_by_thread_id_internal", return_value=None):
            with patch.object(provider, "_create_backend", return_value=mock_backend):
                result = provider.get_or_create(thread_id="new-thread")

        assert result.id == "new-pod-001"

    def test_thread_id_precedes_sandbox_id(self):
        """When both thread_id and sandbox_id are given, thread_id wins."""
        provider = KubernetesProvider(_raw_config())
        mock_backend = _make_mock_backend("tid-found")
        mock_sandbox = KubernetesSandbox(backend=mock_backend)

        with patch.object(provider, "_find_by_thread_id_internal", return_value=mock_sandbox):
            result = provider.get_or_create(
                sandbox_id="some-sandbox-id",
                thread_id="my-thread",
            )

        assert result.id == "tid-found"

    def test_in_process_cache_checked_for_thread_id(self):
        """thread_id in cache returns cached sandbox without API call."""
        provider = KubernetesProvider(_raw_config())
        mock_backend = _make_mock_backend("cached-pod")
        provider._active_backends["cached-pod"] = mock_backend
        provider._thread_id_map["my-thread"] = "cached-pod"

        # Should NOT call the k8s API
        with patch.object(provider, "_find_by_thread_id_raw") as mock_raw_lookup:
            result = provider.get_or_create(thread_id="my-thread")
            mock_raw_lookup.assert_not_called()

        assert result.id == "cached-pod"

    def test_thread_id_stored_in_map_after_create(self):
        """After creating a sandbox for a thread, thread_id is cached."""
        provider = KubernetesProvider(_raw_config())
        mock_backend = _make_mock_backend("new-sandbox")

        with patch.object(provider, "_find_by_thread_id_internal", return_value=None):
            with patch.object(provider, "_create_backend", return_value=mock_backend):
                provider.get_or_create(thread_id="stored-thread")

        assert provider._thread_id_map.get("stored-thread") == "new-sandbox"


# ---------------------------------------------------------------------------
# find_by_thread_id (raw mode)
# ---------------------------------------------------------------------------


class TestFindByThreadIdRaw:
    def test_find_by_thread_id_returns_sandbox(self):
        provider = KubernetesProvider(_raw_config())
        mock_backend = _make_mock_backend("found-pod")
        mock_sandbox = KubernetesSandbox(backend=mock_backend)

        with patch.object(provider, "_find_by_thread_id_internal", return_value=mock_sandbox):
            result = provider.find_by_thread_id("my-thread")

        assert result is not None
        assert result.id == "found-pod"

    def test_find_by_thread_id_not_found_returns_none(self):
        provider = KubernetesProvider(_raw_config())

        with patch.object(provider, "_find_by_thread_id_internal", return_value=None):
            result = provider.find_by_thread_id("nonexistent-thread")

        assert result is None

    def test_find_by_thread_id_calls_raw_backend(self):
        """For raw mode, the lookup uses list_namespaced_pod."""
        from unittest.mock import MagicMock
        from langchain_kubernetes.backends.raw import RawK8sBackend

        provider = KubernetesProvider(_raw_config())
        mock_pod = MagicMock()
        mock_pod.status.phase = "Running"
        mock_pod.metadata.name = "deepagents-abc123"
        mock_pod.metadata.namespace = "default"
        mock_pod.metadata.labels = {"deepagents.langchain.com/sandbox-id": "abc123"}

        mock_pod_list = MagicMock()
        mock_pod_list.items = [mock_pod]

        mock_core_v1 = MagicMock()
        mock_core_v1.list_namespaced_pod.return_value = mock_pod_list
        mock_networking_v1 = MagicMock()

        with patch(
            "langchain_kubernetes.backends.raw._load_k8s_clients",
            return_value=(mock_core_v1, mock_networking_v1),
        ):
            backend = RawK8sBackend.find_by_thread_id(_raw_config(), "my-thread")

        assert backend is not None
        assert backend.id == "abc123"

    def test_find_by_thread_id_skips_non_running_pods(self):
        """Non-Running Pods are not returned."""
        from langchain_kubernetes.backends.raw import RawK8sBackend

        mock_pod = MagicMock()
        mock_pod.status.phase = "Pending"
        mock_pod_list = MagicMock()
        mock_pod_list.items = [mock_pod]

        mock_core_v1 = MagicMock()
        mock_core_v1.list_namespaced_pod.return_value = mock_pod_list
        mock_networking_v1 = MagicMock()

        with patch(
            "langchain_kubernetes.backends.raw._load_k8s_clients",
            return_value=(mock_core_v1, mock_networking_v1),
        ):
            backend = RawK8sBackend.find_by_thread_id(_raw_config(), "my-thread")

        assert backend is None
