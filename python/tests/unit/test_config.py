"""Unit tests for KubernetesProviderConfig."""

from __future__ import annotations

from langchain_kubernetes.config import KubernetesProviderConfig


class TestKubernetesProviderConfigDefaults:
    def test_default_namespace(self):
        cfg = KubernetesProviderConfig()
        assert cfg.namespace == "deepagents-sandboxes"

    def test_namespace_per_sandbox_false_by_default(self):
        assert KubernetesProviderConfig().namespace_per_sandbox is False

    def test_block_network_true_by_default(self):
        assert KubernetesProviderConfig().block_network is True

    def test_default_image(self):
        cfg = KubernetesProviderConfig()
        assert cfg.image  # non-empty

    def test_default_exec_timeout(self):
        cfg = KubernetesProviderConfig()
        assert cfg.default_exec_timeout == 30 * 60

    def test_startup_timeout_positive(self):
        assert KubernetesProviderConfig().startup_timeout > 0

    def test_extra_env_empty_by_default(self):
        assert KubernetesProviderConfig().extra_env == {}

    def test_kubeconfig_none_by_default(self):
        assert KubernetesProviderConfig().kubeconfig is None

    def test_context_none_by_default(self):
        assert KubernetesProviderConfig().context is None


class TestKubernetesProviderConfigCustom:
    def test_custom_namespace(self):
        cfg = KubernetesProviderConfig(namespace="my-ns")
        assert cfg.namespace == "my-ns"

    def test_namespace_per_sandbox_enabled(self):
        cfg = KubernetesProviderConfig(namespace_per_sandbox=True)
        assert cfg.namespace_per_sandbox is True

    def test_network_blocking_disabled(self):
        cfg = KubernetesProviderConfig(block_network=False)
        assert cfg.block_network is False

    def test_custom_resources(self):
        cfg = KubernetesProviderConfig(cpu_limit="4", memory_limit="2Gi")
        assert cfg.cpu_limit == "4"
        assert cfg.memory_limit == "2Gi"

    def test_extra_env_stored(self):
        cfg = KubernetesProviderConfig(extra_env={"KEY": "val"})
        assert cfg.extra_env["KEY"] == "val"

    def test_independent_extra_env_instances(self):
        """Each instance should have its own extra_env dict."""
        cfg1 = KubernetesProviderConfig()
        cfg2 = KubernetesProviderConfig()
        cfg1.extra_env["FOO"] = "bar"
        assert "FOO" not in cfg2.extra_env

    def test_none_resource_limits_allowed(self):
        cfg = KubernetesProviderConfig(cpu_limit=None, memory_limit=None)
        assert cfg.cpu_limit is None
        assert cfg.memory_limit is None
