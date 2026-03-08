"""Unit tests for KubernetesProviderConfig."""

from __future__ import annotations

import pytest

from langchain_kubernetes.config import KubernetesProviderConfig


class TestKubernetesProviderConfigDefaults:
    def test_default_namespace(self):
        cfg = KubernetesProviderConfig(template_name="my-template")
        assert cfg.namespace == "default"

    def test_default_connection_mode(self):
        cfg = KubernetesProviderConfig(template_name="my-template")
        assert cfg.connection_mode == "tunnel"

    def test_default_server_port(self):
        cfg = KubernetesProviderConfig(template_name="my-template")
        assert cfg.server_port == 8888

    def test_default_startup_timeout(self):
        cfg = KubernetesProviderConfig(template_name="my-template")
        assert cfg.startup_timeout_seconds == 120

    def test_default_exec_timeout(self):
        cfg = KubernetesProviderConfig(template_name="my-template")
        assert cfg.default_exec_timeout == 60 * 30

    def test_default_gateway_name_none(self):
        cfg = KubernetesProviderConfig(template_name="my-template")
        assert cfg.gateway_name is None

    def test_default_api_url_none(self):
        cfg = KubernetesProviderConfig(template_name="my-template")
        assert cfg.api_url is None

    def test_default_gateway_namespace(self):
        cfg = KubernetesProviderConfig(template_name="my-template")
        assert cfg.gateway_namespace == "default"


class TestKubernetesProviderConfigCustom:
    def test_template_name_stored(self):
        cfg = KubernetesProviderConfig(template_name="gpu-sandbox")
        assert cfg.template_name == "gpu-sandbox"

    def test_custom_namespace(self):
        cfg = KubernetesProviderConfig(template_name="t", namespace="my-ns")
        assert cfg.namespace == "my-ns"

    def test_gateway_mode(self):
        cfg = KubernetesProviderConfig(
            template_name="t",
            connection_mode="gateway",
            gateway_name="my-gateway",
        )
        assert cfg.connection_mode == "gateway"
        assert cfg.gateway_name == "my-gateway"

    def test_direct_mode(self):
        cfg = KubernetesProviderConfig(
            template_name="t",
            connection_mode="direct",
            api_url="http://sandbox-router.example.com",
        )
        assert cfg.connection_mode == "direct"
        assert cfg.api_url == "http://sandbox-router.example.com"

    def test_custom_server_port(self):
        cfg = KubernetesProviderConfig(template_name="t", server_port=3000)
        assert cfg.server_port == 3000

    def test_custom_startup_timeout(self):
        cfg = KubernetesProviderConfig(template_name="t", startup_timeout_seconds=60)
        assert cfg.startup_timeout_seconds == 60

    def test_template_name_required(self):
        with pytest.raises(TypeError):
            KubernetesProviderConfig()  # type: ignore[call-arg]
