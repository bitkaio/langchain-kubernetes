"""Unit tests for KubernetesSandboxManager."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from langchain_kubernetes.config import KubernetesProviderConfig
from langchain_kubernetes.manager import KubernetesSandboxManager, _extract_thread_id
from langchain_kubernetes.sandbox import KubernetesSandbox


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _raw_config(**kwargs) -> KubernetesProviderConfig:
    defaults = {"mode": "raw"}
    defaults.update(kwargs)
    return KubernetesProviderConfig(**defaults)


def _make_mock_sandbox(sandbox_id: str) -> MagicMock:
    sb = MagicMock(spec=KubernetesSandbox)
    sb.id = sandbox_id
    return sb


def _make_manager(**kwargs) -> KubernetesSandboxManager:
    return KubernetesSandboxManager(
        _raw_config(),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# _extract_thread_id
# ---------------------------------------------------------------------------


class TestExtractThreadId:
    def test_extracts_from_dict_configurable(self):
        config = {"configurable": {"thread_id": "my-thread"}}
        assert _extract_thread_id(config) == "my-thread"

    def test_generates_uuid_when_missing(self):
        thread_id = _extract_thread_id({})
        assert len(thread_id) == 36  # UUID format
        assert "-" in thread_id

    def test_generates_uuid_for_empty_configurable(self):
        thread_id = _extract_thread_id({"configurable": {}})
        assert len(thread_id) > 0

    def test_extracts_from_object_with_configurable(self):
        config = MagicMock()
        config.configurable = {"thread_id": "obj-thread"}
        assert _extract_thread_id(config) == "obj-thread"


# ---------------------------------------------------------------------------
# backend_factory
# ---------------------------------------------------------------------------


class TestBackendFactory:
    def test_backend_factory_creates_per_thread(self):
        """Different thread_ids produce different sandboxes."""
        manager = _make_manager()

        mock_sb1 = _make_mock_sandbox("sb-thread-1")
        mock_sb2 = _make_mock_sandbox("sb-thread-2")

        def mock_get_or_create(**kwargs):
            tid = kwargs.get("thread_id")
            return mock_sb1 if tid == "thread-1" else mock_sb2

        with patch.object(manager._provider, "get_or_create", side_effect=mock_get_or_create):
            factory = manager.backend_factory

            result1 = factory({"configurable": {"thread_id": "thread-1"}})
            result2 = factory({"configurable": {"thread_id": "thread-2"}})

        assert result1.id == "sb-thread-1"
        assert result2.id == "sb-thread-2"

    def test_backend_factory_caches_same_thread(self):
        """Same thread_id returns the same sandbox instance."""
        manager = _make_manager()
        mock_sb = _make_mock_sandbox("cached-sb")

        call_count = 0

        def mock_get_or_create(**kwargs):
            nonlocal call_count
            call_count += 1
            return mock_sb

        with patch.object(manager._provider, "get_or_create", side_effect=mock_get_or_create):
            factory = manager.backend_factory
            r1 = factory({"configurable": {"thread_id": "same-thread"}})
            r2 = factory({"configurable": {"thread_id": "same-thread"}})

        assert r1 is r2
        assert call_count == 1  # only one API call

    def test_backend_factory_missing_thread_id_generates_uuid(self):
        """Missing thread_id generates a UUID and logs a warning."""
        manager = _make_manager()
        mock_sb = _make_mock_sandbox("uuid-sb")

        with patch.object(manager._provider, "get_or_create", return_value=mock_sb):
            factory = manager.backend_factory
            result = factory({})

        assert result is not None

    def test_backend_factory_passes_ttl(self):
        """Manager TTL settings are passed to get_or_create."""
        manager = _make_manager(ttl_seconds=3600, ttl_idle_seconds=600)
        mock_sb = _make_mock_sandbox("ttl-sb")
        captured_kwargs: dict = {}

        def capture(**kwargs):
            captured_kwargs.update(kwargs)
            return mock_sb

        with patch.object(manager._provider, "get_or_create", side_effect=capture):
            factory = manager.backend_factory
            factory({"configurable": {"thread_id": "ttl-thread"}})

        assert captured_kwargs.get("ttl_seconds") == 3600
        assert captured_kwargs.get("ttl_idle_seconds") == 600

    def test_backend_factory_passes_default_labels(self):
        """Manager default_labels are forwarded."""
        manager = _make_manager(default_labels={"env": "test"})
        mock_sb = _make_mock_sandbox("label-sb")
        captured_kwargs: dict = {}

        def capture(**kwargs):
            captured_kwargs.update(kwargs)
            return mock_sb

        with patch.object(manager._provider, "get_or_create", side_effect=capture):
            factory = manager.backend_factory
            factory({"configurable": {"thread_id": "label-thread"}})

        assert captured_kwargs.get("labels") == {"env": "test"}


# ---------------------------------------------------------------------------
# get_sandbox
# ---------------------------------------------------------------------------


class TestGetSandbox:
    def test_get_sandbox_returns_cached(self):
        manager = _make_manager()
        mock_sb = _make_mock_sandbox("get-sb")
        manager._cache["my-thread"] = mock_sb

        result = manager.get_sandbox("my-thread")
        assert result is mock_sb

    def test_get_sandbox_returns_none_when_missing(self):
        manager = _make_manager()
        assert manager.get_sandbox("nonexistent") is None


# ---------------------------------------------------------------------------
# shutdown()
# ---------------------------------------------------------------------------


class TestShutdown:
    def test_shutdown_deletes_all_sandboxes(self):
        manager = _make_manager()
        mock_sb1 = _make_mock_sandbox("sb-1")
        mock_sb2 = _make_mock_sandbox("sb-2")
        manager._cache["thread-1"] = mock_sb1
        manager._cache["thread-2"] = mock_sb2

        deleted_ids = []

        def mock_delete(*, sandbox_id, **kwargs):
            deleted_ids.append(sandbox_id)

        with patch.object(manager._provider, "delete", side_effect=mock_delete):
            manager.shutdown()

        assert set(deleted_ids) == {"sb-1", "sb-2"}

    def test_shutdown_clears_cache(self):
        manager = _make_manager()
        mock_sb = _make_mock_sandbox("sb-1")
        manager._cache["thread-1"] = mock_sb

        with patch.object(manager._provider, "delete"):
            manager.shutdown()

        assert len(manager._cache) == 0

    def test_shutdown_continues_on_error(self):
        """shutdown() logs errors but doesn't raise."""
        manager = _make_manager()
        mock_sb = _make_mock_sandbox("err-sb")
        manager._cache["err-thread"] = mock_sb

        with patch.object(
            manager._provider, "delete", side_effect=RuntimeError("delete failed")
        ):
            manager.shutdown()  # must not raise

        assert len(manager._cache) == 0


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


class TestContextManager:
    def test_context_manager_calls_shutdown(self):
        manager = _make_manager()
        mock_sb = _make_mock_sandbox("ctx-sb")
        manager._cache["ctx-thread"] = mock_sb

        with patch.object(manager, "shutdown") as mock_shutdown:
            with manager:
                pass
            mock_shutdown.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_context_manager_calls_ashutdown(self):
        manager = _make_manager()

        async def _noop():
            pass

        with patch.object(manager, "ashutdown", return_value=_noop()) as mock_ashutdown:
            async with manager:
                pass
            mock_ashutdown.assert_called_once()


# ---------------------------------------------------------------------------
# Async variants
# ---------------------------------------------------------------------------


class TestAsyncBackendFactory:
    @pytest.mark.asyncio
    async def test_abackend_factory_returns_sandbox(self):
        manager = _make_manager()
        mock_sb = _make_mock_sandbox("async-sb")

        with patch.object(manager._provider, "get_or_create", return_value=mock_sb):
            result = await manager.abackend_factory(
                {"configurable": {"thread_id": "async-thread"}}
            )

        assert result.id == "async-sb"
