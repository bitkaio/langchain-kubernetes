"""Integration tests for KubernetesProvider lifecycle (require a live kind cluster)."""

from __future__ import annotations

import pytest

from langchain_kubernetes.provider import KubernetesProvider
from langchain_kubernetes.sandbox import KubernetesSandbox


@pytest.mark.integration
class TestProviderCreateDelete:
    def test_create_returns_kubernetes_sandbox(self, provider):
        sandbox = provider.get_or_create()
        try:
            assert isinstance(sandbox, KubernetesSandbox)
        finally:
            provider.delete(sandbox_id=sandbox.id)

    def test_delete_is_idempotent(self, provider):
        sandbox = provider.get_or_create()
        sid = sandbox.id
        provider.delete(sandbox_id=sid)
        # Second delete should not raise
        provider.delete(sandbox_id=sid)

    def test_list_includes_created_sandbox(self, provider):
        sandbox = provider.get_or_create()
        try:
            sandboxes = provider.list()
            ids = [s.id for s in sandboxes]
            assert sandbox.id in ids
        finally:
            provider.delete(sandbox_id=sandbox.id)

    def test_list_excludes_deleted_sandbox(self, provider):
        sandbox = provider.get_or_create()
        sid = sandbox.id
        provider.delete(sandbox_id=sid)

        import time
        time.sleep(3)  # Allow deletion to propagate

        sandboxes = provider.list()
        ids = [s.id for s in sandboxes]
        assert sid not in ids

    def test_multiple_sandboxes_independent(self, provider):
        s1 = provider.get_or_create()
        s2 = provider.get_or_create()
        try:
            assert s1.id != s2.id
            r1 = s1.execute("echo s1")
            r2 = s2.execute("echo s2")
            assert "s1" in r1.output
            assert "s2" in r2.output
        finally:
            provider.delete(sandbox_id=s1.id)
            provider.delete(sandbox_id=s2.id)


@pytest.mark.integration
class TestProviderNamespacePerSandbox:
    def test_sandbox_id_contains_namespace(self, provider_ns_per_sandbox):
        sandbox = provider_ns_per_sandbox.get_or_create()
        try:
            assert "/" in sandbox.id
        finally:
            provider_ns_per_sandbox.delete(sandbox_id=sandbox.id)

    def test_namespace_deleted_on_sandbox_delete(self, provider_ns_per_sandbox):
        import kubernetes.client as k8s_client
        import kubernetes.config as k8s_config
        import kubernetes.client.exceptions as k8s_exc

        sandbox = provider_ns_per_sandbox.get_or_create()
        namespace = sandbox.id.split("/")[0]
        provider_ns_per_sandbox.delete(sandbox_id=sandbox.id)

        import time
        time.sleep(3)

        k8s_config.load_kube_config()
        core_v1 = k8s_client.CoreV1Api()
        try:
            ns = core_v1.read_namespace(namespace)
            # Namespace may be in Terminating phase
            assert ns.status.phase in ("Terminating", None)
        except k8s_exc.ApiException as exc:
            assert exc.status == 404  # fully deleted


@pytest.mark.integration
class TestProviderAsync:
    async def test_aget_or_create(self, provider):
        sandbox = await provider.aget_or_create()
        try:
            result = sandbox.execute("echo async")
            assert "async" in result.output
        finally:
            await provider.adelete(sandbox_id=sandbox.id)

    async def test_alist(self, provider):
        sandbox = await provider.aget_or_create()
        try:
            sandboxes = await provider.alist()
            ids = [s.id for s in sandboxes]
            assert sandbox.id in ids
        finally:
            await provider.adelete(sandbox_id=sandbox.id)
