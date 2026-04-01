"""Unit tests for KubernetesSandboxManager."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from langchain_kubernetes.config import KubernetesProviderConfig
from langchain_kubernetes.manager import DEFAULT_SANDBOX_STATE_KEY, KubernetesSandboxManager
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
    return KubernetesSandboxManager(_raw_config(), **kwargs)


# ---------------------------------------------------------------------------
# _aget_or_reconnect
# ---------------------------------------------------------------------------


class TestGetOrReconnect:
    @pytest.mark.asyncio
    async def test_passes_sandbox_id_to_provider(self):
        manager = _make_manager()
        mock_sb = _make_mock_sandbox("existing-sb")

        with patch.object(
            manager._provider, "aget_or_create", AsyncMock(return_value=mock_sb)
        ) as mock_create:
            result = await manager._aget_or_reconnect("existing-sb")
            mock_create.assert_awaited_once_with(
                sandbox_id="existing-sb",
                labels=None,
                ttl_seconds=None,
                ttl_idle_seconds=None,
            )

        assert result.id == "existing-sb"

    @pytest.mark.asyncio
    async def test_passes_none_for_new_sandbox(self):
        manager = _make_manager()
        mock_sb = _make_mock_sandbox("new-sb")

        with patch.object(
            manager._provider, "aget_or_create", AsyncMock(return_value=mock_sb)
        ) as mock_create:
            result = await manager._aget_or_reconnect(None)
            call_kwargs = mock_create.call_args[1]
            assert call_kwargs["sandbox_id"] is None

        assert result.id == "new-sb"

    @pytest.mark.asyncio
    async def test_forwards_ttl_settings(self):
        manager = _make_manager(ttl_seconds=3600, ttl_idle_seconds=600)
        mock_sb = _make_mock_sandbox("ttl-sb")

        with patch.object(
            manager._provider, "aget_or_create", AsyncMock(return_value=mock_sb)
        ) as mock_create:
            await manager._aget_or_reconnect(None)
            call_kwargs = mock_create.call_args[1]

        assert call_kwargs["ttl_seconds"] == 3600
        assert call_kwargs["ttl_idle_seconds"] == 600

    @pytest.mark.asyncio
    async def test_forwards_default_labels(self):
        manager = _make_manager(default_labels={"env": "prod"})
        mock_sb = _make_mock_sandbox("label-sb")

        with patch.object(
            manager._provider, "aget_or_create", AsyncMock(return_value=mock_sb)
        ) as mock_create:
            await manager._aget_or_reconnect(None)
            call_kwargs = mock_create.call_args[1]

        assert call_kwargs["labels"] == {"env": "prod"}


# ---------------------------------------------------------------------------
# create_agent_node
# ---------------------------------------------------------------------------


class TestCreateAgentNode:
    def test_returns_callable(self):
        manager = _make_manager()
        node = manager.create_agent_node(model=MagicMock())
        assert callable(node)

    def test_accepts_custom_state_key(self):
        manager = _make_manager()
        node = manager.create_agent_node(model=MagicMock(), state_sandbox_key="my_sandbox")
        assert callable(node)


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------


class TestCleanup:
    def test_delegates_to_provider(self):
        manager = _make_manager()
        mock_result = MagicMock()

        with patch.object(manager._provider, "cleanup", return_value=mock_result) as mock_cleanup:
            result = manager.cleanup()
            mock_cleanup.assert_called_once_with(None)

        assert result is mock_result

    def test_passes_max_idle_seconds(self):
        manager = _make_manager()

        with patch.object(manager._provider, "cleanup", return_value=MagicMock()) as mock_cleanup:
            manager.cleanup(max_idle_seconds=600)
            mock_cleanup.assert_called_once_with(600)


# ---------------------------------------------------------------------------
# shutdown
# ---------------------------------------------------------------------------


class TestShutdown:
    def test_calls_provider_cleanup(self):
        manager = _make_manager()
        mock_result = MagicMock()
        mock_result.deleted = ["sb-1"]

        with patch.object(manager._provider, "cleanup", return_value=mock_result) as mock_cleanup:
            manager.shutdown()
            mock_cleanup.assert_called_once()

    def test_does_not_raise_on_error(self):
        manager = _make_manager()

        with patch.object(
            manager._provider, "cleanup", side_effect=RuntimeError("k8s unreachable")
        ):
            manager.shutdown()  # must not raise

    @pytest.mark.asyncio
    async def test_ashutdown_runs_without_error(self):
        manager = _make_manager()
        mock_result = MagicMock()
        mock_result.deleted = []

        with patch.object(manager._provider, "cleanup", return_value=mock_result):
            await manager.ashutdown()  # must not raise


# ---------------------------------------------------------------------------
# Context managers
# ---------------------------------------------------------------------------


class TestContextManager:
    def test_sync_context_manager_calls_shutdown(self):
        manager = _make_manager()

        with patch.object(manager, "shutdown") as mock_shutdown:
            with manager:
                pass
            mock_shutdown.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_context_manager_calls_ashutdown(self):
        manager = _make_manager()

        with patch.object(manager, "ashutdown", AsyncMock()) as mock_ashutdown:
            async with manager:
                pass
            mock_ashutdown.assert_called_once()


# ---------------------------------------------------------------------------
# _make_backend_factory
# ---------------------------------------------------------------------------


class TestMakeBackendFactory:
    def _fake_config(self, thread_id: str | None) -> dict:
        return {"configurable": {"thread_id": thread_id}} if thread_id else {}

    def test_raises_when_no_thread_id(self):
        manager = _make_manager()
        factory = manager._make_backend_factory()

        with patch(
            "langchain_core.runnables.config.ensure_config",
            return_value=self._fake_config(None),
        ):
            with pytest.raises(RuntimeError, match="no thread_id"):
                factory(runtime=None)

    def test_lazy_acquires_when_sandbox_not_cached(self):
        manager = _make_manager()
        mock_sb = _make_mock_sandbox("lazy-sb")
        factory = manager._make_backend_factory()

        with patch(
            "langchain_core.runnables.config.ensure_config",
            return_value=self._fake_config("thread-1"),
        ), patch.object(
            manager._provider, "get_or_create", return_value=mock_sb
        ) as mock_create:
            result = factory(runtime=None)

        assert result is mock_sb
        assert manager._sandbox_by_thread["thread-1"] is mock_sb
        mock_create.assert_called_once()

    def test_returns_cached_sandbox(self):
        manager = _make_manager()
        mock_sb = _make_mock_sandbox("cached-sb")
        manager._sandbox_by_thread["thread-42"] = mock_sb
        factory = manager._make_backend_factory()

        with patch(
            "langchain_core.runnables.config.ensure_config",
            return_value=self._fake_config("thread-42"),
        ):
            result = factory(runtime=None)

        assert result is mock_sb

    def test_lazy_acquire_does_not_call_provider_when_cached(self):
        manager = _make_manager()
        mock_sb = _make_mock_sandbox("cached-sb")
        manager._sandbox_by_thread["thread-42"] = mock_sb
        factory = manager._make_backend_factory()

        with patch(
            "langchain_core.runnables.config.ensure_config",
            return_value=self._fake_config("thread-42"),
        ), patch.object(
            manager._provider, "get_or_create"
        ) as mock_create:
            factory(runtime=None)

        mock_create.assert_not_called()


# ---------------------------------------------------------------------------
# create_setup_node
# ---------------------------------------------------------------------------


class TestCreateSetupNode:
    @pytest.mark.asyncio
    async def test_new_sandbox_populates_cache_and_returns_id(self):
        manager = _make_manager()
        mock_sb = _make_mock_sandbox("new-sandbox-id")

        with patch.object(
            manager, "_aget_or_reconnect", AsyncMock(return_value=mock_sb)
        ):
            node = manager.create_setup_node()
            state = {DEFAULT_SANDBOX_STATE_KEY: None}
            config = {"configurable": {"thread_id": "thread-new"}}
            updates = await node(state, config)

        assert manager._sandbox_by_thread["thread-new"] is mock_sb
        assert updates[DEFAULT_SANDBOX_STATE_KEY] == "new-sandbox-id"

    @pytest.mark.asyncio
    async def test_existing_sandbox_populates_cache_and_returns_empty_updates(self):
        manager = _make_manager()
        mock_sb = _make_mock_sandbox("existing-id")

        with patch.object(
            manager, "_aget_or_reconnect", AsyncMock(return_value=mock_sb)
        ):
            node = manager.create_setup_node()
            state = {DEFAULT_SANDBOX_STATE_KEY: "existing-id"}
            config = {"configurable": {"thread_id": "thread-existing"}}
            updates = await node(state, config)

        assert manager._sandbox_by_thread["thread-existing"] is mock_sb
        assert updates == {}

    @pytest.mark.asyncio
    async def test_raises_when_no_thread_id(self):
        manager = _make_manager()
        mock_sb = _make_mock_sandbox("sb-1")

        with patch.object(
            manager, "_aget_or_reconnect", AsyncMock(return_value=mock_sb)
        ):
            node = manager.create_setup_node()
            with pytest.raises(RuntimeError, match="no thread_id"):
                await node({DEFAULT_SANDBOX_STATE_KEY: None}, {})


# ---------------------------------------------------------------------------
# create_agent — returns deepagent directly
# ---------------------------------------------------------------------------


class TestCreateAgent:
    def test_returns_deepagent_directly(self):
        manager = _make_manager()
        mock_model = MagicMock()
        mock_agent = MagicMock()

        with patch("deepagents.create_deep_agent", return_value=mock_agent) as mock_create:
            result = manager.create_agent(mock_model)

        assert result is mock_agent
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args[1] if mock_create.call_args[1] else {}
        call_args = mock_create.call_args[0] if mock_create.call_args[0] else ()
        # First positional arg is the model
        assert call_args[0] is mock_model

    def test_passes_backend_factory(self):
        manager = _make_manager()
        mock_model = MagicMock()
        mock_agent = MagicMock()

        with patch("deepagents.create_deep_agent", return_value=mock_agent) as mock_create:
            manager.create_agent(mock_model)

        call_kwargs = mock_create.call_args[1]
        assert "backend" in call_kwargs
        assert callable(call_kwargs["backend"])

    def test_passes_checkpointer(self):
        manager = _make_manager()
        mock_model = MagicMock()
        mock_checkpointer = MagicMock()
        mock_agent = MagicMock()

        with patch("deepagents.create_deep_agent", return_value=mock_agent) as mock_create:
            manager.create_agent(mock_model, checkpointer=mock_checkpointer)

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["checkpointer"] is mock_checkpointer

    def test_forwards_extra_kwargs(self):
        manager = _make_manager()
        mock_model = MagicMock()
        mock_agent = MagicMock()

        with patch("deepagents.create_deep_agent", return_value=mock_agent) as mock_create:
            manager.create_agent(mock_model, system_prompt="Be helpful")

        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["system_prompt"] == "Be helpful"
