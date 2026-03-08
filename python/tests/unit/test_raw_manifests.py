"""Unit tests for raw_manifests — pure manifest builder functions."""

from __future__ import annotations

import pytest

from langchain_kubernetes.backends.raw_manifests import (
    LABEL_COMPONENT,
    LABEL_MANAGED_BY,
    LABEL_SANDBOX_ID,
    build_namespace_manifest,
    build_network_policy_manifest,
    build_pod_manifest,
    build_resource_quota_manifest,
    sandbox_labels,
)
from langchain_kubernetes.config import KubernetesProviderConfig


def _raw_config(**kwargs) -> KubernetesProviderConfig:
    defaults = {"mode": "raw"}
    defaults.update(kwargs)
    return KubernetesProviderConfig(**defaults)


SANDBOX_ID = "abc12345"


# ---------------------------------------------------------------------------
# sandbox_labels
# ---------------------------------------------------------------------------


class TestSandboxLabels:
    def test_contains_managed_by(self):
        labels = sandbox_labels(SANDBOX_ID)
        assert labels[LABEL_MANAGED_BY] == "deepagents"

    def test_contains_component(self):
        labels = sandbox_labels(SANDBOX_ID)
        assert labels[LABEL_COMPONENT] == "sandbox"

    def test_contains_sandbox_id(self):
        labels = sandbox_labels(SANDBOX_ID)
        assert labels[LABEL_SANDBOX_ID] == SANDBOX_ID


# ---------------------------------------------------------------------------
# build_pod_manifest
# ---------------------------------------------------------------------------


class TestBuildPodManifest:
    def _build(self, **kwargs) -> dict:
        return build_pod_manifest(_raw_config(**kwargs), SANDBOX_ID)

    def test_api_version_and_kind(self):
        m = self._build()
        assert m["apiVersion"] == "v1"
        assert m["kind"] == "Pod"

    def test_pod_name(self):
        m = self._build()
        assert m["metadata"]["name"] == f"deepagents-{SANDBOX_ID}"

    def test_labels_present(self):
        m = self._build()
        labels = m["metadata"]["labels"]
        assert labels[LABEL_MANAGED_BY] == "deepagents"
        assert labels[LABEL_SANDBOX_ID] == SANDBOX_ID

    def test_container_name_is_sandbox(self):
        m = self._build()
        containers = m["spec"]["containers"]
        assert len(containers) == 1
        assert containers[0]["name"] == "sandbox"

    def test_container_image(self):
        m = self._build(image="python:3.11-slim")
        assert m["spec"]["containers"][0]["image"] == "python:3.11-slim"

    def test_default_image(self):
        m = self._build()
        assert m["spec"]["containers"][0]["image"] == "python:3.12-slim"

    def test_security_context_allow_privilege_escalation_false(self):
        ctx = self._build()["spec"]["containers"][0]["securityContext"]
        assert ctx["allowPrivilegeEscalation"] is False

    def test_security_context_drop_all(self):
        ctx = self._build()["spec"]["containers"][0]["securityContext"]
        assert "ALL" in ctx["capabilities"]["drop"]

    def test_security_context_seccomp_profile(self):
        ctx = self._build()["spec"]["containers"][0]["securityContext"]
        assert ctx["seccompProfile"]["type"] == "RuntimeDefault"

    def test_pod_security_context_run_as_non_root(self):
        ctx = self._build()["spec"]["securityContext"]
        assert ctx["runAsNonRoot"] is True

    def test_pod_security_context_run_as_user(self):
        ctx = self._build(run_as_user=2000)["spec"]["securityContext"]
        assert ctx["runAsUser"] == 2000

    def test_pod_security_context_run_as_group(self):
        ctx = self._build(run_as_group=3000)["spec"]["securityContext"]
        assert ctx["runAsGroup"] == 3000

    def test_automount_service_account_token_false(self):
        assert self._build()["spec"]["automountServiceAccountToken"] is False

    def test_restart_policy_never(self):
        assert self._build()["spec"]["restartPolicy"] == "Never"

    def test_resource_requests(self):
        m = self._build(cpu_request="200m", memory_request="512Mi")
        resources = m["spec"]["containers"][0]["resources"]
        assert resources["requests"]["cpu"] == "200m"
        assert resources["requests"]["memory"] == "512Mi"

    def test_resource_limits(self):
        m = self._build(cpu_limit="2000m", memory_limit="2Gi")
        resources = m["spec"]["containers"][0]["resources"]
        assert resources["limits"]["cpu"] == "2000m"
        assert resources["limits"]["memory"] == "2Gi"

    def test_ephemeral_storage_limit(self):
        m = self._build(ephemeral_storage_limit="10Gi")
        limits = m["spec"]["containers"][0]["resources"]["limits"]
        assert limits["ephemeral-storage"] == "10Gi"

    def test_env_vars_included(self):
        m = self._build(env={"FOO": "bar", "BAZ": "qux"})
        env = m["spec"]["containers"][0]["env"]
        env_map = {e["name"]: e["value"] for e in env}
        assert env_map["FOO"] == "bar"
        assert env_map["BAZ"] == "qux"

    def test_no_env_when_empty(self):
        m = self._build()
        assert "env" not in m["spec"]["containers"][0]

    def test_service_account_included(self):
        m = self._build(service_account="my-sa")
        assert m["spec"]["serviceAccountName"] == "my-sa"

    def test_no_service_account_by_default(self):
        m = self._build()
        assert "serviceAccountName" not in m["spec"]

    def test_node_selector_included(self):
        m = self._build(node_selector={"gpu": "true"})
        assert m["spec"]["nodeSelector"]["gpu"] == "true"

    def test_image_pull_secrets(self):
        m = self._build(image_pull_secrets=["my-secret"])
        assert m["spec"]["imagePullSecrets"][0]["name"] == "my-secret"

    def test_extra_annotations_applied(self):
        m = self._build(extra_annotations={"owner": "team-a"})
        assert m["metadata"]["annotations"]["owner"] == "team-a"

    def test_pod_template_overrides_applied(self):
        m = self._build(pod_template_overrides={"terminationGracePeriodSeconds": 0})
        assert m["spec"]["terminationGracePeriodSeconds"] == 0

    def test_pod_template_overrides_deep_merge(self):
        m = self._build(pod_template_overrides={"securityContext": {"fsGroup": 1000}})
        # Existing keys survive
        assert m["spec"]["securityContext"]["runAsNonRoot"] is True
        # Override key added
        assert m["spec"]["securityContext"]["fsGroup"] == 1000

    def test_custom_seccomp_profile(self):
        m = self._build(seccomp_profile="Localhost")
        ctx = m["spec"]["containers"][0]["securityContext"]
        assert ctx["seccompProfile"]["type"] == "Localhost"

    def test_volume_mounts_included(self):
        vm = [{"name": "data", "mountPath": "/data"}]
        m = self._build(volume_mounts=vm)
        assert m["spec"]["containers"][0]["volumeMounts"] == vm

    def test_volumes_included(self):
        vol = [{"name": "data", "emptyDir": {}}]
        m = self._build(volumes=vol)
        assert m["spec"]["volumes"] == vol

    def test_init_containers_included(self):
        ic = [{"name": "init", "image": "busybox", "command": ["sh", "-c", "echo hi"]}]
        m = self._build(init_containers=ic)
        assert m["spec"]["initContainers"] == ic


# ---------------------------------------------------------------------------
# build_namespace_manifest
# ---------------------------------------------------------------------------


class TestBuildNamespaceManifest:
    def test_api_version_and_kind(self):
        m = build_namespace_manifest("deepagents-abc")
        assert m["apiVersion"] == "v1"
        assert m["kind"] == "Namespace"

    def test_name(self):
        m = build_namespace_manifest("deepagents-abc")
        assert m["metadata"]["name"] == "deepagents-abc"

    def test_managed_by_label(self):
        m = build_namespace_manifest("deepagents-abc")
        assert m["metadata"]["labels"][LABEL_MANAGED_BY] == "deepagents"

    def test_extra_labels_merged(self):
        m = build_namespace_manifest("ns", labels={"env": "test"})
        assert m["metadata"]["labels"]["env"] == "test"
        # standard labels still present
        assert m["metadata"]["labels"][LABEL_MANAGED_BY] == "deepagents"


# ---------------------------------------------------------------------------
# build_network_policy_manifest
# ---------------------------------------------------------------------------


class TestBuildNetworkPolicyManifest:
    def _build(self) -> dict:
        return build_network_policy_manifest(SANDBOX_ID, "deepagents-abc")

    def test_api_version_and_kind(self):
        m = self._build()
        assert m["apiVersion"] == "networking.k8s.io/v1"
        assert m["kind"] == "NetworkPolicy"

    def test_name_includes_sandbox_id(self):
        m = self._build()
        assert m["metadata"]["name"] == f"deepagents-deny-all-{SANDBOX_ID}"

    def test_namespace(self):
        m = build_network_policy_manifest(SANDBOX_ID, "my-ns")
        assert m["metadata"]["namespace"] == "my-ns"

    def test_pod_selector_matches_sandbox_label(self):
        m = self._build()
        selector = m["spec"]["podSelector"]["matchLabels"]
        assert selector[LABEL_SANDBOX_ID] == SANDBOX_ID

    def test_policy_types_ingress_and_egress(self):
        m = self._build()
        assert "Ingress" in m["spec"]["policyTypes"]
        assert "Egress" in m["spec"]["policyTypes"]

    def test_ingress_empty(self):
        m = self._build()
        assert m["spec"]["ingress"] == []

    def test_egress_empty(self):
        m = self._build()
        assert m["spec"]["egress"] == []

    def test_labels_present(self):
        m = self._build()
        assert m["metadata"]["labels"][LABEL_MANAGED_BY] == "deepagents"


# ---------------------------------------------------------------------------
# build_resource_quota_manifest
# ---------------------------------------------------------------------------


class TestBuildResourceQuotaManifest:
    def test_api_version_and_kind(self):
        m = build_resource_quota_manifest("my-ns", _raw_config())
        assert m["apiVersion"] == "v1"
        assert m["kind"] == "ResourceQuota"

    def test_name_and_namespace(self):
        m = build_resource_quota_manifest("my-ns", _raw_config())
        assert m["metadata"]["name"] == "deepagents-quota"
        assert m["metadata"]["namespace"] == "my-ns"

    def test_cpu_limits_from_config(self):
        cfg = _raw_config(cpu_request="500m", cpu_limit="2000m")
        m = build_resource_quota_manifest("ns", cfg)
        assert m["spec"]["hard"]["requests.cpu"] == "500m"
        assert m["spec"]["hard"]["limits.cpu"] == "2000m"

    def test_memory_limits_from_config(self):
        cfg = _raw_config(memory_request="1Gi", memory_limit="4Gi")
        m = build_resource_quota_manifest("ns", cfg)
        assert m["spec"]["hard"]["requests.memory"] == "1Gi"
        assert m["spec"]["hard"]["limits.memory"] == "4Gi"
