"""Integration tests for KubernetesProvider lifecycle (require a live cluster)."""

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
            assert sandbox.id
        finally:
            provider.delete(sandbox_id=sandbox.id)

    def test_delete_is_idempotent(self, provider):
        sandbox = provider.get_or_create()
        sid = sandbox.id
        provider.delete(sandbox_id=sid)
        # Second delete should be a no-op, not raise
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
class TestProviderAsync:
    @pytest.mark.asyncio
    async def test_aget_or_create(self, provider):
        sandbox = await provider.aget_or_create()
        try:
            result = sandbox.execute("echo async")
            assert "async" in result.output
        finally:
            await provider.adelete(sandbox_id=sandbox.id)

    @pytest.mark.asyncio
    async def test_alist(self, provider):
        sandbox = await provider.aget_or_create()
        try:
            sandboxes = await provider.alist()
            ids = [s.id for s in sandboxes]
            assert sandbox.id in ids
        finally:
            await provider.adelete(sandbox_id=sandbox.id)
