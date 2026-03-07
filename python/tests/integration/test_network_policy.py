"""Integration tests verifying NetworkPolicy enforcement (require a kind cluster)."""

from __future__ import annotations

import pytest

from langchain_kubernetes.config import KubernetesProviderConfig
from langchain_kubernetes.provider import KubernetesProvider


@pytest.mark.integration
class TestNetworkPolicyBlocksOutbound:
    def test_network_blocked_sandbox_cannot_reach_internet(self, provider):
        """When block_network=True the sandbox should not be able to reach the internet."""
        sandbox = provider.get_or_create()
        try:
            # curl should fail (network is blocked); timeout guards against hanging
            result = sandbox.execute("curl -s --max-time 3 http://example.com", timeout=10)
            # Either a non-zero exit code or an empty/error output indicates blocking
            assert result.exit_code != 0 or not result.output.strip()
        finally:
            provider.delete(sandbox_id=sandbox.id)

    def test_network_allowed_sandbox_can_reach_internet(self):
        """When block_network=False the sandbox should be able to reach DNS."""
        config = KubernetesProviderConfig(
            namespace="deepagents-sandboxes",
            block_network=False,
            startup_timeout=120,
            image="python:3.12-slim",
        )
        provider = KubernetesProvider(config=config)
        sandbox = provider.get_or_create()
        try:
            # ping the cluster DNS; should succeed when network is open
            result = sandbox.execute("nslookup kubernetes.default.svc.cluster.local", timeout=10)
            assert result.exit_code == 0
        finally:
            provider.delete(sandbox_id=sandbox.id)


@pytest.mark.integration
class TestNetworkPolicyLifecycle:
    def test_network_policy_created_on_sandbox_create(self, provider):
        import kubernetes.client as k8s_client
        import kubernetes.config as k8s_config

        sandbox = provider.get_or_create()
        try:
            # Inspect the cluster to verify the NetworkPolicy exists
            k8s_config.load_kube_config()
            net_v1 = k8s_client.NetworkingV1Api()
            namespace = sandbox.id.split("/")[0] if "/" in sandbox.id else provider._config.namespace
            policies = net_v1.list_namespaced_network_policy(namespace)
            policy_names = [p.metadata.name for p in policies.items]
            assert any("deepagents-sandbox-deny-all" in name for name in policy_names)
        finally:
            provider.delete(sandbox_id=sandbox.id)

    def test_network_policy_deleted_with_sandbox(self, provider):
        import time

        import kubernetes.client as k8s_client
        import kubernetes.config as k8s_config

        sandbox = provider.get_or_create()
        namespace = sandbox.id.split("/")[0] if "/" in sandbox.id else provider._config.namespace
        provider.delete(sandbox_id=sandbox.id)

        time.sleep(2)

        k8s_config.load_kube_config()
        net_v1 = k8s_client.NetworkingV1Api()
        policies = net_v1.list_namespaced_network_policy(namespace)
        policy_names = [p.metadata.name for p in policies.items]
        # The deny-all policy for this sandbox should be gone
        sandbox_short_id = sandbox.id.split("/")[-1].removeprefix("deepagents-sandbox-")
        assert not any(sandbox_short_id in name for name in policy_names)
