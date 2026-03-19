"""Unit tests for TTL annotations, last-activity tracking, and cleanup()."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from langchain_kubernetes._labels import (
    ANN_CREATED_AT,
    ANN_LAST_ACTIVITY,
    ANN_TTL_IDLE_SECONDS,
    ANN_TTL_SECONDS,
)
from langchain_kubernetes._types import CleanupResult, SandboxInfo, SandboxListResponse
from langchain_kubernetes.config import KubernetesProviderConfig
from langchain_kubernetes.provider import KubernetesProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _raw_config(**kwargs) -> KubernetesProviderConfig:
    defaults = {"mode": "raw"}
    defaults.update(kwargs)
    return KubernetesProviderConfig(**defaults)


def _agent_sandbox_config(**kwargs) -> KubernetesProviderConfig:
    defaults = {"mode": "agent-sandbox", "template_name": "test-template"}
    defaults.update(kwargs)
    return KubernetesProviderConfig(**defaults)


def _make_mock_backend(sandbox_id: str = "test-001") -> MagicMock:
    backend = MagicMock()
    backend.id = sandbox_id
    backend.cleanup = MagicMock()
    backend._ttl_idle_seconds = None
    return backend


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ---------------------------------------------------------------------------
# TTL annotations on create
# ---------------------------------------------------------------------------


class TestTTLAnnotationsOnCreate:
    def test_ttl_seconds_annotation_set(self):
        """Creating with ttl_seconds sets TTL annotation on the Pod."""
        provider = KubernetesProvider(_raw_config(ttl_seconds=3600))

        with patch.object(provider, "_create_raw_backend") as mock_create:
            mock_backend = _make_mock_backend("ttl-pod-1")
            mock_create.return_value = mock_backend
            provider.get_or_create()

        call_kwargs = mock_create.call_args[1]
        annotations = call_kwargs.get("extra_annotations", {})
        assert ANN_TTL_SECONDS in annotations
        assert annotations[ANN_TTL_SECONDS] == "3600"
        assert ANN_CREATED_AT in annotations

    def test_ttl_idle_seconds_annotation_set(self):
        """Creating with ttl_idle_seconds sets idle TTL annotation."""
        provider = KubernetesProvider(_raw_config(ttl_idle_seconds=600))

        with patch.object(provider, "_create_raw_backend") as mock_create:
            mock_backend = _make_mock_backend("ttl-pod-2")
            mock_create.return_value = mock_backend
            provider.get_or_create()

        call_kwargs = mock_create.call_args[1]
        annotations = call_kwargs.get("extra_annotations", {})
        assert ANN_TTL_IDLE_SECONDS in annotations
        assert annotations[ANN_TTL_IDLE_SECONDS] == "600"

    def test_per_call_ttl_overrides_config(self):
        """Per-call ttl_seconds overrides config.ttl_seconds."""
        provider = KubernetesProvider(_raw_config(ttl_seconds=3600))

        with patch.object(provider, "_create_raw_backend") as mock_create:
            mock_backend = _make_mock_backend("ttl-pod-3")
            mock_create.return_value = mock_backend
            provider.get_or_create(ttl_seconds=1800)

        call_kwargs = mock_create.call_args[1]
        annotations = call_kwargs.get("extra_annotations", {})
        assert annotations[ANN_TTL_SECONDS] == "1800"

    def test_no_ttl_no_annotation(self):
        """Without TTL config, no TTL annotations are set."""
        provider = KubernetesProvider(_raw_config())

        with patch.object(provider, "_create_raw_backend") as mock_create:
            mock_backend = _make_mock_backend("no-ttl-pod")
            mock_create.return_value = mock_backend
            provider.get_or_create()

        call_kwargs = mock_create.call_args[1]
        annotations = call_kwargs.get("extra_annotations", {})
        assert ANN_TTL_SECONDS not in annotations
        assert ANN_TTL_IDLE_SECONDS not in annotations


# ---------------------------------------------------------------------------
# Last-activity tracking in execute()
# ---------------------------------------------------------------------------


class TestLastActivityTracking:
    def test_execute_updates_last_activity_when_idle_ttl_configured(self):
        """execute() patches last-activity annotation when ttl_idle_seconds is set."""
        from langchain_kubernetes.backends.raw import RawK8sBackend

        mock_core_v1 = MagicMock()
        mock_core_v1.patch_namespaced_pod = MagicMock()

        mock_output = ("output", 0, False)
        with patch("langchain_kubernetes.backends.raw.exec_command", return_value=mock_output):
            backend = RawK8sBackend(
                sandbox_id="act-pod",
                pod_name="deepagents-act-pod",
                namespace="default",
                container="sandbox",
                core_v1=mock_core_v1,
                networking_v1=MagicMock(),
                config=_raw_config(),
                ttl_idle_seconds=300,
            )
            backend.execute("echo hello")

        mock_core_v1.patch_namespaced_pod.assert_called_once()
        call_kwargs = mock_core_v1.patch_namespaced_pod.call_args
        patch_body = call_kwargs[1]["body"]
        assert ANN_LAST_ACTIVITY in patch_body["metadata"]["annotations"]

    def test_execute_does_not_patch_without_idle_ttl(self):
        """execute() does NOT patch when ttl_idle_seconds is not set."""
        from langchain_kubernetes.backends.raw import RawK8sBackend

        mock_core_v1 = MagicMock()

        mock_output = ("output", 0, False)
        with patch("langchain_kubernetes.backends.raw.exec_command", return_value=mock_output):
            backend = RawK8sBackend(
                sandbox_id="no-act-pod",
                pod_name="deepagents-no-act-pod",
                namespace="default",
                container="sandbox",
                core_v1=mock_core_v1,
                networking_v1=MagicMock(),
                config=_raw_config(),
                ttl_idle_seconds=None,
            )
            backend.execute("echo hello")

        mock_core_v1.patch_namespaced_pod.assert_not_called()

    def test_execute_patch_failure_does_not_raise(self):
        """A failed last-activity patch is swallowed (fire-and-forget)."""
        from langchain_kubernetes.backends.raw import RawK8sBackend

        mock_core_v1 = MagicMock()
        mock_core_v1.patch_namespaced_pod.side_effect = RuntimeError("network error")

        mock_output = ("output", 0, False)
        with patch("langchain_kubernetes.backends.raw.exec_command", return_value=mock_output):
            backend = RawK8sBackend(
                sandbox_id="err-pod",
                pod_name="deepagents-err-pod",
                namespace="default",
                container="sandbox",
                core_v1=mock_core_v1,
                networking_v1=MagicMock(),
                config=_raw_config(),
                ttl_idle_seconds=300,
            )
            # Must not raise
            result = backend.execute("echo hello")

        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# cleanup() — deletes expired, keeps active
# ---------------------------------------------------------------------------


class TestCleanup:
    def _make_provider_with_sandboxes(
        self, sandboxes: list[SandboxInfo]
    ) -> KubernetesProvider:
        provider = KubernetesProvider(_raw_config())
        with patch.object(
            provider,
            "list",
            return_value=SandboxListResponse(sandboxes=sandboxes),
        ):
            return provider

    def test_cleanup_deletes_expired_by_ttl(self):
        """Sandboxes past their ttl_seconds are deleted."""
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=7200)).isoformat()
        sandboxes = [
            SandboxInfo(
                id="old-sandbox",
                namespace="default",
                annotations={ANN_TTL_SECONDS: "3600", ANN_CREATED_AT: old_time},
                status="running",
            ),
        ]
        provider = KubernetesProvider(_raw_config())

        with patch.object(provider, "list", return_value=SandboxListResponse(sandboxes=sandboxes)):
            with patch.object(provider, "_delete_raw_pod") as mock_del:
                result = provider.cleanup()

        mock_del.assert_called_once_with("old-sandbox", "default")
        assert "old-sandbox" in result.deleted
        assert result.kept == 0

    def test_cleanup_keeps_active_sandboxes(self):
        """Sandboxes within TTL are not deleted."""
        recent_time = (datetime.now(timezone.utc) - timedelta(seconds=100)).isoformat()
        sandboxes = [
            SandboxInfo(
                id="fresh-sandbox",
                namespace="default",
                annotations={ANN_TTL_SECONDS: "3600", ANN_CREATED_AT: recent_time},
                status="running",
            ),
        ]
        provider = KubernetesProvider(_raw_config())

        with patch.object(provider, "list", return_value=SandboxListResponse(sandboxes=sandboxes)):
            with patch.object(provider, "_delete_raw_pod") as mock_del:
                result = provider.cleanup()

        mock_del.assert_not_called()
        assert result.deleted == []
        assert result.kept == 1

    def test_cleanup_deletes_idle(self):
        """Sandboxes past their idle TTL are deleted."""
        idle_time = (datetime.now(timezone.utc) - timedelta(seconds=700)).isoformat()
        sandboxes = [
            SandboxInfo(
                id="idle-sandbox",
                namespace="default",
                annotations={
                    ANN_TTL_IDLE_SECONDS: "600",
                    ANN_LAST_ACTIVITY: idle_time,
                },
                status="running",
            ),
        ]
        provider = KubernetesProvider(_raw_config())

        with patch.object(provider, "list", return_value=SandboxListResponse(sandboxes=sandboxes)):
            with patch.object(provider, "_delete_raw_pod") as mock_del:
                result = provider.cleanup()

        mock_del.assert_called_once()
        assert "idle-sandbox" in result.deleted

    def test_cleanup_max_idle_seconds_overrides_annotation(self):
        """max_idle_seconds param overrides per-sandbox annotation."""
        idle_time = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()
        sandboxes = [
            SandboxInfo(
                id="param-idle",
                namespace="default",
                annotations={
                    ANN_TTL_IDLE_SECONDS: "600",  # would keep it
                    ANN_LAST_ACTIVITY: idle_time,
                },
                status="running",
            ),
        ]
        provider = KubernetesProvider(_raw_config())

        with patch.object(provider, "list", return_value=SandboxListResponse(sandboxes=sandboxes)):
            with patch.object(provider, "_delete_raw_pod") as mock_del:
                result = provider.cleanup(max_idle_seconds=300)

        mock_del.assert_called_once()
        assert "param-idle" in result.deleted

    def test_cleanup_mixed(self):
        """Some expired, some kept."""
        old = (datetime.now(timezone.utc) - timedelta(seconds=7200)).isoformat()
        recent = (datetime.now(timezone.utc) - timedelta(seconds=100)).isoformat()
        sandboxes = [
            SandboxInfo(
                id="expired",
                namespace="default",
                annotations={ANN_TTL_SECONDS: "3600", ANN_CREATED_AT: old},
                status="running",
            ),
            SandboxInfo(
                id="fresh",
                namespace="default",
                annotations={ANN_TTL_SECONDS: "3600", ANN_CREATED_AT: recent},
                status="running",
            ),
        ]
        provider = KubernetesProvider(_raw_config())

        with patch.object(provider, "list", return_value=SandboxListResponse(sandboxes=sandboxes)):
            with patch.object(provider, "_delete_raw_pod") as mock_del:
                result = provider.cleanup()

        assert result.deleted == ["expired"]
        assert result.kept == 1

    def test_cleanup_agent_sandbox_mode(self):
        """cleanup() uses _delete_agent_sandbox_claim in agent-sandbox mode."""
        old = (datetime.now(timezone.utc) - timedelta(seconds=7200)).isoformat()
        sandboxes = [
            SandboxInfo(
                id="expired-claim",
                namespace="default",
                annotations={ANN_TTL_SECONDS: "3600", ANN_CREATED_AT: old},
                status="running",
            ),
        ]
        provider = KubernetesProvider(_agent_sandbox_config())

        with patch.object(provider, "list", return_value=SandboxListResponse(sandboxes=sandboxes)):
            with patch.object(provider, "_delete_agent_sandbox_claim") as mock_del:
                result = provider.cleanup()

        mock_del.assert_called_once_with("expired-claim")
        assert "expired-claim" in result.deleted
