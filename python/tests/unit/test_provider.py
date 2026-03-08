"""Unit tests for KubernetesProvider — mocks SandboxClient."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from langchain_kubernetes._provider_base import SandboxNotFoundError
from langchain_kubernetes.config import KubernetesProviderConfig
from langchain_kubernetes.provider import KubernetesProvider
from langchain_kubernetes.sandbox import KubernetesSandbox


def _make_config(**kwargs) -> KubernetesProviderConfig:
    defaults = {"template_name": "test-template"}
    defaults.update(kwargs)
    return KubernetesProviderConfig(**defaults)


def _make_mock_client(sandbox_name: str = "test-sandbox-abc") -> MagicMock:
    client = MagicMock()
    client.sandbox_name = sandbox_name
    client.claim_name = None
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    return client


class TestKubernetesProviderGetOrCreate:
    def test_creates_sandbox_client_with_template_name(self):
        config = _make_config(template_name="my-template")
        provider = KubernetesProvider(config)

        mock_client = _make_mock_client("sandbox-xyz")

        with patch("langchain_kubernetes.provider._build_client", return_value=mock_client):
            sandbox = provider.get_or_create()

        mock_client.__enter__.assert_called_once()
        assert isinstance(sandbox, KubernetesSandbox)
        assert sandbox.id == "sandbox-xyz"

    def test_sandbox_id_from_client_sandbox_name(self):
        config = _make_config()
        provider = KubernetesProvider(config)
        mock_client = _make_mock_client("my-sandbox-001")

        with patch("langchain_kubernetes.provider._build_client", return_value=mock_client):
            sandbox = provider.get_or_create()

        assert sandbox.id == "my-sandbox-001"

    def test_falls_back_to_claim_name_when_sandbox_name_none(self):
        config = _make_config()
        provider = KubernetesProvider(config)
        mock_client = _make_mock_client()
        mock_client.sandbox_name = None
        mock_client.claim_name = "claim-abc"

        with patch("langchain_kubernetes.provider._build_client", return_value=mock_client):
            sandbox = provider.get_or_create()

        assert sandbox.id == "claim-abc"

    def test_reconnect_to_active_sandbox(self):
        config = _make_config()
        provider = KubernetesProvider(config)
        mock_client = _make_mock_client("active-sandbox")

        with patch("langchain_kubernetes.provider._build_client", return_value=mock_client):
            sb1 = provider.get_or_create()

        # Reconnect by ID — should NOT call _build_client again
        with patch("langchain_kubernetes.provider._build_client") as mock_build:
            sb2 = provider.get_or_create(sandbox_id="active-sandbox")
            mock_build.assert_not_called()

        assert sb2.id == "active-sandbox"

    def test_raises_not_found_for_unknown_sandbox_id(self):
        config = _make_config()
        provider = KubernetesProvider(config)

        with pytest.raises(SandboxNotFoundError, match="unknown-id"):
            provider.get_or_create(sandbox_id="unknown-id")


class TestKubernetesProviderList:
    def test_list_empty_initially(self):
        provider = KubernetesProvider(_make_config())
        assert provider.list() == []

    def test_list_returns_active_sandboxes(self):
        provider = KubernetesProvider(_make_config())
        mock_client = _make_mock_client("sb-001")

        with patch("langchain_kubernetes.provider._build_client", return_value=mock_client):
            provider.get_or_create()

        sandboxes = provider.list()
        assert len(sandboxes) == 1
        assert sandboxes[0].id == "sb-001"

    def test_list_multiple_sandboxes(self):
        provider = KubernetesProvider(_make_config())
        clients = [_make_mock_client(f"sb-{i:03d}") for i in range(3)]

        for client in clients:
            with patch("langchain_kubernetes.provider._build_client", return_value=client):
                provider.get_or_create()

        assert len(provider.list()) == 3


class TestKubernetesProviderDelete:
    def test_delete_exits_client_context(self):
        provider = KubernetesProvider(_make_config())
        mock_client = _make_mock_client("sb-del")

        with patch("langchain_kubernetes.provider._build_client", return_value=mock_client):
            provider.get_or_create()

        provider.delete(sandbox_id="sb-del")

        mock_client.__exit__.assert_called_once_with(None, None, None)

    def test_delete_removes_from_active(self):
        provider = KubernetesProvider(_make_config())
        mock_client = _make_mock_client("sb-del")

        with patch("langchain_kubernetes.provider._build_client", return_value=mock_client):
            provider.get_or_create()

        assert len(provider.list()) == 1
        provider.delete(sandbox_id="sb-del")
        assert len(provider.list()) == 0

    def test_delete_nonexistent_is_noop(self):
        provider = KubernetesProvider(_make_config())
        # Should not raise
        provider.delete(sandbox_id="does-not-exist")

    def test_delete_exit_error_is_logged_not_raised(self):
        provider = KubernetesProvider(_make_config())
        mock_client = _make_mock_client("sb-err")
        mock_client.__exit__.side_effect = RuntimeError("cleanup failed")

        with patch("langchain_kubernetes.provider._build_client", return_value=mock_client):
            provider.get_or_create()

        # Should not raise even if __exit__ fails
        provider.delete(sandbox_id="sb-err")


class TestKubernetesProviderBuildClient:
    def test_tunnel_mode_passes_correct_kwargs(self):
        config = _make_config(
            template_name="tpl",
            namespace="ns",
            connection_mode="tunnel",
            server_port=9000,
            startup_timeout_seconds=60,
        )

        with patch("langchain_kubernetes.provider._import_sandbox_client") as mock_import:
            mock_cls = MagicMock()
            mock_import.return_value = mock_cls
            from langchain_kubernetes.provider import _build_client

            _build_client(config)

        mock_cls.assert_called_once_with(
            template_name="tpl",
            namespace="ns",
            server_port=9000,
            sandbox_ready_timeout=60,
        )

    def test_gateway_mode_requires_gateway_name(self):
        config = _make_config(connection_mode="gateway", gateway_name=None)
        from langchain_kubernetes.provider import _build_client

        with patch("langchain_kubernetes.provider._import_sandbox_client"):
            with pytest.raises(ValueError, match="gateway_name"):
                _build_client(config)

    def test_direct_mode_requires_api_url(self):
        config = _make_config(connection_mode="direct", api_url=None)
        from langchain_kubernetes.provider import _build_client

        with patch("langchain_kubernetes.provider._import_sandbox_client"):
            with pytest.raises(ValueError, match="api_url"):
                _build_client(config)

    def test_missing_sdk_raises_import_error(self):
        config = _make_config()
        provider = KubernetesProvider(config)

        with patch(
            "langchain_kubernetes.provider._import_sandbox_client",
            side_effect=ImportError("k8s-agent-sandbox package not installed"),
        ):
            with pytest.raises(ImportError, match="k8s-agent-sandbox"):
                provider.get_or_create()


class TestKubernetesProviderAsync:
    @pytest.mark.asyncio
    async def test_aget_or_create(self):
        provider = KubernetesProvider(_make_config())
        mock_client = _make_mock_client("async-sb")

        with patch("langchain_kubernetes.provider._build_client", return_value=mock_client):
            sandbox = await provider.aget_or_create()

        assert sandbox.id == "async-sb"

    @pytest.mark.asyncio
    async def test_adelete(self):
        provider = KubernetesProvider(_make_config())
        mock_client = _make_mock_client("async-del")

        with patch("langchain_kubernetes.provider._build_client", return_value=mock_client):
            await provider.aget_or_create()

        await provider.adelete(sandbox_id="async-del")
        mock_client.__exit__.assert_called_once()
