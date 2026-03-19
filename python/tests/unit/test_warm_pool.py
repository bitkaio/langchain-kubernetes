"""Unit tests for raw-mode warm pool."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from langchain_kubernetes._labels import LABEL_POOL_STATUS, POOL_STATUS_ACTIVE, POOL_STATUS_WARM
from langchain_kubernetes.config import KubernetesProviderConfig
from langchain_kubernetes.provider import KubernetesProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _raw_config(**kwargs) -> KubernetesProviderConfig:
    defaults = {"mode": "raw"}
    defaults.update(kwargs)
    return KubernetesProviderConfig(**defaults)


def _make_running_pod(name: str, labels: dict | None = None) -> MagicMock:
    pod = MagicMock()
    pod.metadata.name = name
    pod.metadata.namespace = "default"
    pod.metadata.labels = labels or {}
    pod.status.phase = "Running"
    return pod


def _make_pod_list(pods: list) -> MagicMock:
    pl = MagicMock()
    pl.items = pods
    return pl


# ---------------------------------------------------------------------------
# Warm pool — claim
# ---------------------------------------------------------------------------


class TestWarmPoolClaim:
    def test_claim_warm_pod_when_available(self):
        """get_or_create claims a warm Pod when pool has available Pods."""
        from langchain_kubernetes.backends.raw import RawK8sBackend

        warm_pod = _make_running_pod(
            "deepagents-warm-abc1",
            labels={
                "deepagents.langchain.com/sandbox-id": "warm-abc1",
                LABEL_POOL_STATUS: POOL_STATUS_WARM,
            },
        )
        warm_list = _make_pod_list([warm_pod])

        mock_core_v1 = MagicMock()
        mock_core_v1.list_namespaced_pod.return_value = warm_list
        mock_core_v1.patch_namespaced_pod = MagicMock()
        mock_networking_v1 = MagicMock()

        config = _raw_config(warm_pool_size=2)

        with patch(
            "langchain_kubernetes.backends.raw._load_k8s_clients",
            return_value=(mock_core_v1, mock_networking_v1),
        ):
            backend = RawK8sBackend.claim_warm_pod(config, thread_id="my-thread")

        assert backend is not None
        assert backend.id == "warm-abc1"
        # Pod was patched with thread_id label
        mock_core_v1.patch_namespaced_pod.assert_called_once()
        patch_call = mock_core_v1.patch_namespaced_pod.call_args
        patch_labels = patch_call[1]["body"]["metadata"]["labels"]
        assert patch_labels[LABEL_POOL_STATUS] == POOL_STATUS_ACTIVE

    def test_claim_warm_pod_returns_none_when_empty_pool(self):
        """Returns None when no warm Pods are available."""
        from langchain_kubernetes.backends.raw import RawK8sBackend

        mock_core_v1 = MagicMock()
        mock_core_v1.list_namespaced_pod.return_value = _make_pod_list([])
        mock_networking_v1 = MagicMock()

        config = _raw_config(warm_pool_size=2)

        with patch(
            "langchain_kubernetes.backends.raw._load_k8s_clients",
            return_value=(mock_core_v1, mock_networking_v1),
        ):
            backend = RawK8sBackend.claim_warm_pod(config, thread_id="no-pool-thread")

        assert backend is None


class TestWarmPoolExhaustion:
    def test_fallback_to_cold_create_when_pool_empty(self):
        """When pool is empty, get_or_create falls back to creating a new Pod."""
        provider = KubernetesProvider(_raw_config(warm_pool_size=2))
        provider._warm_pool_initialised = True  # skip init

        mock_backend = MagicMock()
        mock_backend.id = "cold-pod"

        with patch.object(provider, "_find_by_thread_id_internal", return_value=None):
            with patch("langchain_kubernetes.backends.raw.RawK8sBackend.claim_warm_pod", return_value=None):
                with patch.object(provider, "_create_backend", return_value=mock_backend):
                    result = provider.get_or_create(thread_id="fallback-thread")

        assert result.id == "cold-pod"

    def test_warm_pod_used_before_cold_create(self):
        """When pool has Pods, claim_warm_pod is called before _create_backend."""
        provider = KubernetesProvider(_raw_config(warm_pool_size=2))
        provider._warm_pool_initialised = True

        mock_backend = MagicMock()
        mock_backend.id = "warm-pod"
        mock_sandbox = MagicMock()
        mock_sandbox.id = "warm-pod"

        with patch.object(provider, "_find_by_thread_id_internal", return_value=None):
            with patch(
                "langchain_kubernetes.backends.raw.RawK8sBackend.claim_warm_pod",
                return_value=mock_backend,
            ) as mock_claim:
                with patch.object(provider, "_create_backend") as mock_create:
                    result = provider.get_or_create(thread_id="warm-thread")

        mock_claim.assert_called_once()
        mock_create.assert_not_called()


class TestWarmPoolReplenish:
    def test_replenish_after_delete(self):
        """After deleting a sandbox, replenish is scheduled."""
        provider = KubernetesProvider(_raw_config(warm_pool_size=2))
        provider._warm_pool_initialised = True

        mock_backend = MagicMock()
        mock_backend.id = "replenish-pod"
        mock_backend.cleanup = MagicMock()
        provider._active_backends["replenish-pod"] = mock_backend

        with patch.object(provider, "_schedule_replenish") as mock_replenish:
            provider.delete(sandbox_id="replenish-pod")
            mock_replenish.assert_called_once()

    def test_no_replenish_when_pool_disabled(self):
        """Replenish is NOT called when warm_pool_size=0."""
        provider = KubernetesProvider(_raw_config(warm_pool_size=0))

        mock_backend = MagicMock()
        mock_backend.id = "no-replenish-pod"
        mock_backend.cleanup = MagicMock()
        provider._active_backends["no-replenish-pod"] = mock_backend

        with patch.object(provider, "_schedule_replenish") as mock_replenish:
            provider.delete(sandbox_id="no-replenish-pod")
            mock_replenish.assert_not_called()


# ---------------------------------------------------------------------------
# pool_status()
# ---------------------------------------------------------------------------


class TestPoolStatus:
    def test_pool_status_raw_mode(self):
        """pool_status() returns correct counts for raw mode."""
        provider = KubernetesProvider(_raw_config(warm_pool_size=3))

        warm_pod = _make_running_pod("deepagents-warm-1")
        active_pod = _make_running_pod("deepagents-active-1")

        mock_core_v1 = MagicMock()
        mock_core_v1.list_namespaced_pod.side_effect = [
            _make_pod_list([warm_pod]),    # warm query
            _make_pod_list([active_pod]),  # active query
        ]

        with patch(
            "langchain_kubernetes.backends.raw.RawK8sBackend.load_k8s_clients",
            return_value=(mock_core_v1, MagicMock()),
        ):
            status = provider.pool_status()

        assert status.available == 1
        assert status.active == 1
        assert status.total == 2
        assert status.target == 3
