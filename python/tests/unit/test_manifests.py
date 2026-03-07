"""Unit tests for manifest builders (no cluster required)."""

from __future__ import annotations

import pytest

from langchain_kubernetes._utils import LABEL_MANAGED_BY, LABEL_SANDBOX_ID, common_labels
from langchain_kubernetes.config import KubernetesProviderConfig
from langchain_kubernetes.manifests import (
    build_namespace_manifest,
    build_network_policy_manifest,
    build_pod_manifest,
)


class TestCommonLabels:
    def test_all_required_keys_present(self):
        labels = common_labels("abc123")
        assert labels["app.kubernetes.io/managed-by"] == "deepagents"
        assert labels["app.kubernetes.io/component"] == "sandbox"
        assert labels["deepagents.langchain.com/sandbox-id"] == "abc123"

    def test_sandbox_id_embedded(self):
        assert common_labels("xyz")["deepagents.langchain.com/sandbox-id"] == "xyz"


class TestNamespaceManifest:
    def test_kind_and_api_version(self):
        m = build_namespace_manifest("my-ns", "sid1")
        assert m["kind"] == "Namespace"
        assert m["apiVersion"] == "v1"

    def test_name_and_labels(self):
        m = build_namespace_manifest("my-ns", "sid1")
        assert m["metadata"]["name"] == "my-ns"
        assert m["metadata"]["labels"][LABEL_SANDBOX_ID] == "sid1"
        assert m["metadata"]["labels"][LABEL_MANAGED_BY] == "deepagents"


class TestPodManifest:
    def _config(self, **overrides) -> KubernetesProviderConfig:
        cfg = KubernetesProviderConfig()
        for k, v in overrides.items():
            setattr(cfg, k, v)
        return cfg

    def test_basic_structure(self):
        m = build_pod_manifest(
            pod_name="deepagents-sandbox-aabb",
            namespace="deepagents-sandboxes",
            sandbox_id="aabb",
            config=self._config(),
        )
        assert m["kind"] == "Pod"
        assert m["apiVersion"] == "v1"
        assert m["metadata"]["name"] == "deepagents-sandbox-aabb"
        assert m["metadata"]["namespace"] == "deepagents-sandboxes"

    def test_labels_on_pod(self):
        m = build_pod_manifest(
            pod_name="deepagents-sandbox-aabb",
            namespace="deepagents-sandboxes",
            sandbox_id="aabb",
            config=self._config(),
        )
        assert m["metadata"]["labels"][LABEL_SANDBOX_ID] == "aabb"

    def test_sleep_infinity_command(self):
        m = build_pod_manifest(
            pod_name="p", namespace="ns", sandbox_id="s", config=self._config()
        )
        assert m["spec"]["containers"][0]["command"] == ["sleep", "infinity"]

    def test_security_context_non_root(self):
        m = build_pod_manifest(
            pod_name="p", namespace="ns", sandbox_id="s", config=self._config()
        )
        sc = m["spec"]["containers"][0]["securityContext"]
        assert sc["runAsNonRoot"] is True
        assert sc["runAsUser"] == 1000
        assert sc["allowPrivilegeEscalation"] is False
        assert "ALL" in sc["capabilities"]["drop"]

    def test_no_service_account_token(self):
        m = build_pod_manifest(
            pod_name="p", namespace="ns", sandbox_id="s", config=self._config()
        )
        assert m["spec"]["automountServiceAccountToken"] is False

    def test_restart_policy_never(self):
        m = build_pod_manifest(
            pod_name="p", namespace="ns", sandbox_id="s", config=self._config()
        )
        assert m["spec"]["restartPolicy"] == "Never"

    def test_resource_limits_present(self):
        cfg = self._config(cpu_limit="1", memory_limit="256Mi")
        m = build_pod_manifest(pod_name="p", namespace="ns", sandbox_id="s", config=cfg)
        resources = m["spec"]["containers"][0]["resources"]
        assert resources["limits"]["cpu"] == "1"
        assert resources["limits"]["memory"] == "256Mi"

    def test_resource_limits_omitted_when_none(self):
        cfg = self._config(cpu_limit=None, memory_limit=None, cpu_request=None, memory_request=None)
        m = build_pod_manifest(pod_name="p", namespace="ns", sandbox_id="s", config=cfg)
        assert "resources" not in m["spec"]["containers"][0]

    def test_extra_env_included(self):
        cfg = self._config(extra_env={"FOO": "bar", "BAZ": "qux"})
        m = build_pod_manifest(pod_name="p", namespace="ns", sandbox_id="s", config=cfg)
        env = {e["name"]: e["value"] for e in m["spec"]["containers"][0]["env"]}
        assert env["FOO"] == "bar"
        assert env["BAZ"] == "qux"

    def test_custom_image(self):
        cfg = self._config(image="my-registry/custom:latest")
        m = build_pod_manifest(pod_name="p", namespace="ns", sandbox_id="s", config=cfg)
        assert m["spec"]["containers"][0]["image"] == "my-registry/custom:latest"


class TestNetworkPolicyManifest:
    def test_kind_and_api_version(self):
        m = build_network_policy_manifest(namespace="ns", sandbox_id="sid")
        assert m["kind"] == "NetworkPolicy"
        assert m["apiVersion"] == "networking.k8s.io/v1"

    def test_deny_all_ingress_egress(self):
        m = build_network_policy_manifest(namespace="ns", sandbox_id="sid")
        spec = m["spec"]
        assert "Ingress" in spec["policyTypes"]
        assert "Egress" in spec["policyTypes"]
        assert spec["ingress"] == []
        assert spec["egress"] == []

    def test_pod_selector_uses_sandbox_id(self):
        m = build_network_policy_manifest(namespace="ns", sandbox_id="abc")
        selector = m["spec"]["podSelector"]["matchLabels"]
        assert selector[LABEL_SANDBOX_ID] == "abc"

    def test_name_includes_sandbox_id(self):
        m = build_network_policy_manifest(namespace="ns", sandbox_id="abc")
        assert "abc" in m["metadata"]["name"]

    def test_namespace_in_metadata(self):
        m = build_network_policy_manifest(namespace="test-ns", sandbox_id="s")
        assert m["metadata"]["namespace"] == "test-ns"
