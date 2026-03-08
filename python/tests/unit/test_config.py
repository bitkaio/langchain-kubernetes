"""Unit tests for KubernetesProviderConfig."""

from __future__ import annotations

import pytest

from langchain_kubernetes.config import KubernetesProviderConfig


# ---------------------------------------------------------------------------
# agent-sandbox mode (default)
# ---------------------------------------------------------------------------


class TestAgentSandboxModeDefaults:
    def test_default_mode_is_agent_sandbox(self):
        cfg = KubernetesProviderConfig(template_name="my-template")
        assert cfg.mode == "agent-sandbox"

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


class TestAgentSandboxModeValidation:
    def test_template_name_required_for_agent_sandbox_mode(self):
        with pytest.raises(ValueError, match="template_name"):
            KubernetesProviderConfig(mode="agent-sandbox")

    def test_template_name_required_for_default_mode(self):
        # Default mode is "agent-sandbox", so no template_name → error
        with pytest.raises(ValueError, match="template_name"):
            KubernetesProviderConfig()

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


# ---------------------------------------------------------------------------
# raw mode
# ---------------------------------------------------------------------------


class TestRawModeDefaults:
    def _raw(self, **kwargs) -> KubernetesProviderConfig:
        return KubernetesProviderConfig(mode="raw", **kwargs)

    def test_mode_raw(self):
        assert self._raw().mode == "raw"

    def test_template_name_not_required_in_raw_mode(self):
        # Should not raise
        cfg = self._raw()
        assert cfg.template_name is None

    def test_default_image(self):
        assert self._raw().image == "python:3.12-slim"

    def test_default_image_pull_policy(self):
        assert self._raw().image_pull_policy == "IfNotPresent"

    def test_default_workdir(self):
        assert self._raw().workdir == "/workspace"

    def test_default_command(self):
        assert self._raw().command == ["sleep", "infinity"]

    def test_default_cpu_request(self):
        assert self._raw().cpu_request == "100m"

    def test_default_cpu_limit(self):
        assert self._raw().cpu_limit == "1000m"

    def test_default_memory_request(self):
        assert self._raw().memory_request == "256Mi"

    def test_default_memory_limit(self):
        assert self._raw().memory_limit == "1Gi"

    def test_default_ephemeral_storage_limit(self):
        assert self._raw().ephemeral_storage_limit == "5Gi"

    def test_default_block_network(self):
        assert self._raw().block_network is True

    def test_default_run_as_user(self):
        assert self._raw().run_as_user == 1000

    def test_default_run_as_group(self):
        assert self._raw().run_as_group == 1000

    def test_default_seccomp_profile(self):
        assert self._raw().seccomp_profile == "RuntimeDefault"

    def test_default_namespace_per_sandbox(self):
        assert self._raw().namespace_per_sandbox is False

    def test_default_pod_ttl(self):
        assert self._raw().pod_ttl_seconds == 3600

    def test_default_service_account_none(self):
        assert self._raw().service_account is None

    def test_default_node_selector_empty(self):
        assert self._raw().node_selector == {}

    def test_default_tolerations_empty(self):
        assert self._raw().tolerations == []

    def test_default_volumes_empty(self):
        assert self._raw().volumes == []

    def test_default_volume_mounts_empty(self):
        assert self._raw().volume_mounts == []

    def test_default_init_containers_empty(self):
        assert self._raw().init_containers == []

    def test_default_pod_template_overrides_none(self):
        assert self._raw().pod_template_overrides is None

    def test_default_extra_annotations_empty(self):
        assert self._raw().extra_annotations == {}

    def test_default_setup_script_none(self):
        assert self._raw().setup_script is None


class TestRawModeCustom:
    def _raw(self, **kwargs) -> KubernetesProviderConfig:
        return KubernetesProviderConfig(mode="raw", **kwargs)

    def test_custom_image(self):
        assert self._raw(image="alpine:3.18").image == "alpine:3.18"

    def test_custom_env(self):
        cfg = self._raw(env={"KEY": "val"})
        assert cfg.env["KEY"] == "val"

    def test_block_network_false(self):
        assert self._raw(block_network=False).block_network is False

    def test_namespace_per_sandbox_true(self):
        assert self._raw(namespace_per_sandbox=True).namespace_per_sandbox is True

    def test_list_fields_are_independent_instances(self):
        c1 = self._raw()
        c2 = self._raw()
        c1.tolerations.append({"key": "x"})
        assert c2.tolerations == []


# ---------------------------------------------------------------------------
# Mode validation
# ---------------------------------------------------------------------------


class TestModeValidation:
    def test_unknown_mode_raises(self):
        with pytest.raises(ValueError, match="Unknown mode"):
            KubernetesProviderConfig(mode="invalid-mode")
