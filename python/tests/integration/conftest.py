"""Pytest fixtures for integration tests against a kind cluster."""

from __future__ import annotations

import uuid
import pytest
import kubernetes.client as k8s_client
import kubernetes.config as k8s_config

from langchain_kubernetes.config import KubernetesProviderConfig
from langchain_kubernetes.provider import KubernetesProvider


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: mark test as integration test requiring a live Kubernetes cluster",
    )


@pytest.fixture(scope="session")
def k8s_core_v1():
    """Session-scoped CoreV1Api pointing at the default kubeconfig context."""
    k8s_config.load_kube_config()
    return k8s_client.CoreV1Api()


@pytest.fixture(scope="session")
def test_namespace(k8s_core_v1):
    """Create a dedicated test namespace and delete it on teardown."""
    ns_name = f"deepagents-test-{uuid.uuid4().hex[:8]}"
    body = k8s_client.V1Namespace(
        metadata=k8s_client.V1ObjectMeta(
            name=ns_name,
            labels={"app.kubernetes.io/managed-by": "deepagents-test"},
        )
    )
    k8s_core_v1.create_namespace(body)
    yield ns_name
    try:
        k8s_core_v1.delete_namespace(
            ns_name,
            body=k8s_client.V1DeleteOptions(propagation_policy="Foreground"),
        )
    except Exception:
        pass


@pytest.fixture()
def provider(test_namespace):
    """KubernetesProvider configured to use the test namespace."""
    config = KubernetesProviderConfig(
        namespace=test_namespace,
        namespace_per_sandbox=False,
        block_network=True,
        startup_timeout=120,
        image="python:3.12-slim",
    )
    return KubernetesProvider(config=config)


@pytest.fixture()
def provider_ns_per_sandbox():
    """KubernetesProvider using one namespace per sandbox."""
    config = KubernetesProviderConfig(
        namespace_per_sandbox=True,
        block_network=True,
        startup_timeout=120,
        image="python:3.12-slim",
    )
    return KubernetesProvider(config=config)
