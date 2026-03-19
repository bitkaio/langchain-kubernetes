"""Unit tests for KubernetesProvider — mocks backends."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from langchain_kubernetes._provider_base import SandboxNotFoundError
from langchain_kubernetes.config import KubernetesProviderConfig
from langchain_kubernetes.provider import (
    KubernetesProvider,
    _build_agent_sandbox_client,
    _import_sandbox_client,
)
from langchain_kubernetes.sandbox import KubernetesSandbox


# ---------------------------------------------------------------------------
# Config helpers
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
    client.claim_name = None
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    return client


def _make_mock_backend(sandbox_id: str = "test-backend-001") -> MagicMock:
    backend = MagicMock()
    backend.id = sandbox_id
    return backend


# ---------------------------------------------------------------------------
# agent-sandbox mode: get_or_create
# ---------------------------------------------------------------------------


class TestGetOrCreateAgentSandbox:
    def test_creates_agent_sandbox_backend(self):
        config = _agent_sandbox_config()
        provider = KubernetesProvider(config)
        mock_client = _make_mock_sandbox_client("sandbox-xyz")

        with patch("langchain_kubernetes.provider._build_agent_sandbox_client", return_value=mock_client):
            sandbox = provider.get_or_create()

        mock_client.__enter__.assert_called_once()
        assert isinstance(sandbox, KubernetesSandbox)
        assert sandbox.id == "sandbox-xyz"

    def test_sandbox_id_from_client_sandbox_name(self):
        provider = KubernetesProvider(_agent_sandbox_config())
        mock_client = _make_mock_sandbox_client("my-sandbox-001")

        with patch("langchain_kubernetes.provider._build_agent_sandbox_client", return_value=mock_client):
            sandbox = provider.get_or_create()

        assert sandbox.id == "my-sandbox-001"

    def test_falls_back_to_claim_name(self):
        provider = KubernetesProvider(_agent_sandbox_config())
        mock_client = _make_mock_sandbox_client()
        mock_client.sandbox_name = None
        mock_client.claim_name = "claim-abc"

        with patch("langchain_kubernetes.provider._build_agent_sandbox_client", return_value=mock_client):
            sandbox = provider.get_or_create()

        assert sandbox.id == "claim-abc"

    def test_reconnect_to_active_sandbox(self):
        provider = KubernetesProvider(_agent_sandbox_config())
        mock_client = _make_mock_sandbox_client("active-sandbox")

        with patch("langchain_kubernetes.provider._build_agent_sandbox_client", return_value=mock_client):
            sb1 = provider.get_or_create()

        with patch("langchain_kubernetes.provider._build_agent_sandbox_client") as mock_build:
            sb2 = provider.get_or_create(sandbox_id="active-sandbox")
            mock_build.assert_not_called()

        assert sb2.id == "active-sandbox"

    def test_raises_not_found_for_unknown_sandbox_id(self):
        provider = KubernetesProvider(_agent_sandbox_config())

        with pytest.raises(SandboxNotFoundError, match="unknown-id"):
            provider.get_or_create(sandbox_id="unknown-id")


# ---------------------------------------------------------------------------
# raw mode: get_or_create
# ---------------------------------------------------------------------------


class TestGetOrCreateRaw:
    def test_creates_raw_backend(self):
        provider = KubernetesProvider(_raw_config())
        mock_backend = _make_mock_backend("rawid001")

        with patch("langchain_kubernetes.provider.KubernetesProvider._create_raw_backend", return_value=mock_backend):
            sandbox = provider.get_or_create()

        assert isinstance(sandbox, KubernetesSandbox)
        assert sandbox.id == "rawid001"

    def test_raw_backend_tracked(self):
        provider = KubernetesProvider(_raw_config())
        mock_backend = _make_mock_backend("rawid002")

        with patch("langchain_kubernetes.provider.KubernetesProvider._create_raw_backend", return_value=mock_backend):
            provider.get_or_create()

        assert "rawid002" in provider._active_backends


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


class TestList:
    def test_list_empty_initially(self):
        provider = KubernetesProvider(_agent_sandbox_config())
        result = provider.list()
        assert result.sandboxes == []

    def test_list_returns_active_sandboxes(self):
        provider = KubernetesProvider(_agent_sandbox_config())
        mock_client = _make_mock_sandbox_client("sb-001")

        with patch("langchain_kubernetes.provider._build_agent_sandbox_client", return_value=mock_client):
            provider.get_or_create()

        result = provider.list()
        assert len(result.sandboxes) == 1
        assert result.sandboxes[0].id == "sb-001"

    def test_list_multiple_sandboxes(self):
        provider = KubernetesProvider(_agent_sandbox_config())
        for i in range(3):
            client = _make_mock_sandbox_client(f"sb-{i:03d}")
            with patch("langchain_kubernetes.provider._build_agent_sandbox_client", return_value=client):
                provider.get_or_create()

        result = provider.list()
        assert len(result.sandboxes) == 3


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


class TestDelete:
    def test_delete_calls_backend_cleanup(self):
        provider = KubernetesProvider(_agent_sandbox_config())
        mock_client = _make_mock_sandbox_client("sb-del")

        with patch("langchain_kubernetes.provider._build_agent_sandbox_client", return_value=mock_client):
            provider.get_or_create()

        provider.delete(sandbox_id="sb-del")

        # AgentSandboxBackend.cleanup() calls __exit__
        mock_client.__exit__.assert_called_once_with(None, None, None)

    def test_delete_removes_from_active(self):
        provider = KubernetesProvider(_agent_sandbox_config())
        mock_client = _make_mock_sandbox_client("sb-del")

        with patch("langchain_kubernetes.provider._build_agent_sandbox_client", return_value=mock_client):
            provider.get_or_create()

        assert len(provider.list().sandboxes) == 1
        provider.delete(sandbox_id="sb-del")
        assert len(provider.list().sandboxes) == 0

    def test_delete_nonexistent_is_noop(self):
        provider = KubernetesProvider(_agent_sandbox_config())
        provider.delete(sandbox_id="does-not-exist")  # must not raise

    def test_delete_raw_backend_calls_cleanup(self):
        provider = KubernetesProvider(_raw_config())
        mock_backend = _make_mock_backend("rawid")

        with patch("langchain_kubernetes.provider.KubernetesProvider._create_raw_backend", return_value=mock_backend):
            provider.get_or_create()

        provider.delete(sandbox_id="rawid")
        mock_backend.cleanup.assert_called_once()


# ---------------------------------------------------------------------------
# agent-sandbox client builder
# ---------------------------------------------------------------------------


class TestBuildAgentSandboxClient:
    def test_tunnel_mode_passes_correct_kwargs(self):
        config = _agent_sandbox_config(
            template_name="tpl",
            namespace="ns",
            connection_mode="tunnel",
            server_port=9000,
            startup_timeout_seconds=60,
        )

        with patch("langchain_kubernetes.provider._import_sandbox_client") as mock_import:
            mock_cls = MagicMock()
            mock_import.return_value = mock_cls
            _build_agent_sandbox_client(config)

        mock_cls.assert_called_once_with(
            template_name="tpl",
            namespace="ns",
            server_port=9000,
            sandbox_ready_timeout=60,
        )

    def test_gateway_mode_requires_gateway_name(self):
        config = _agent_sandbox_config(connection_mode="gateway", gateway_name=None)

        with patch("langchain_kubernetes.provider._import_sandbox_client"):
            with pytest.raises(ValueError, match="gateway_name"):
                _build_agent_sandbox_client(config)

    def test_direct_mode_requires_api_url(self):
        config = _agent_sandbox_config(connection_mode="direct", api_url=None)

        with patch("langchain_kubernetes.provider._import_sandbox_client"):
            with pytest.raises(ValueError, match="api_url"):
                _build_agent_sandbox_client(config)

    def test_missing_sdk_raises_import_error(self):
        provider = KubernetesProvider(_agent_sandbox_config())

        with patch(
            "langchain_kubernetes.provider._import_sandbox_client",
            side_effect=ImportError("k8s-agent-sandbox not installed"),
        ):
            with pytest.raises(ImportError, match="k8s-agent-sandbox"):
                provider.get_or_create()


# ---------------------------------------------------------------------------
# async interface
# ---------------------------------------------------------------------------


class TestAsync:
    @pytest.mark.asyncio
    async def test_aget_or_create(self):
        provider = KubernetesProvider(_agent_sandbox_config())
        mock_client = _make_mock_sandbox_client("async-sb")

        with patch("langchain_kubernetes.provider._build_agent_sandbox_client", return_value=mock_client):
            sandbox = await provider.aget_or_create()

        assert sandbox.id == "async-sb"

    @pytest.mark.asyncio
    async def test_adelete(self):
        provider = KubernetesProvider(_agent_sandbox_config())
        mock_client = _make_mock_sandbox_client("async-del")

        with patch("langchain_kubernetes.provider._build_agent_sandbox_client", return_value=mock_client):
            await provider.aget_or_create()

        await provider.adelete(sandbox_id="async-del")
        mock_client.__exit__.assert_called_once()

    @pytest.mark.asyncio
    async def test_alist(self):
        provider = KubernetesProvider(_agent_sandbox_config())
        mock_client = _make_mock_sandbox_client("async-list-sb")

        with patch("langchain_kubernetes.provider._build_agent_sandbox_client", return_value=mock_client):
            await provider.aget_or_create()

        result = await provider.alist()
        assert len(result.sandboxes) == 1
        assert result.sandboxes[0].id == "async-list-sb"


# ---------------------------------------------------------------------------
# mode dispatch
# ---------------------------------------------------------------------------


class TestModeDispatch:
    def test_agent_sandbox_mode_calls_agent_sandbox_backend(self):
        provider = KubernetesProvider(_agent_sandbox_config())

        with patch.object(provider, "_create_agent_sandbox_backend") as mock_create:
            mock_backend = _make_mock_backend("asb-001")
            mock_create.return_value = mock_backend
            provider.get_or_create()

        mock_create.assert_called_once()

    def test_raw_mode_calls_raw_backend(self):
        provider = KubernetesProvider(_raw_config())

        with patch.object(provider, "_create_raw_backend") as mock_create:
            mock_backend = _make_mock_backend("raw-001")
            mock_create.return_value = mock_backend
            provider.get_or_create()

        mock_create.assert_called_once()
