"""Integration test fixtures.

Requires a kind cluster with the agent-sandbox controller, CRDs, and
sandbox-router installed. Apply a SandboxTemplate before running:

    kubectl apply -f examples/k8s/sandbox-template.yaml

Run integration tests with:

    K8S_INTEGRATION=1 pytest -m integration

Environment variables:
    K8S_INTEGRATION     Set to any non-empty value to enable integration tests.
    SANDBOX_TEMPLATE    SandboxTemplate name (default: "python-sandbox-template").
    SANDBOX_NAMESPACE   Kubernetes namespace (default: "default").
    SANDBOX_TIMEOUT     Startup timeout in seconds (default: 180).
"""

from __future__ import annotations

import os

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks tests as integration tests requiring a live cluster "
        "(deselect with '-m not integration')",
    )


def pytest_collection_modifyitems(config, items):
    if os.environ.get("K8S_INTEGRATION"):
        return  # run everything

    skip_integration = pytest.mark.skip(
        reason="Set K8S_INTEGRATION=1 to run integration tests"
    )
    for item in items:
        if item.get_closest_marker("integration"):
            item.add_marker(skip_integration)


SANDBOX_TEMPLATE = os.environ.get("SANDBOX_TEMPLATE", "python-sandbox-template")
SANDBOX_NAMESPACE = os.environ.get("SANDBOX_NAMESPACE", "default")
SANDBOX_TIMEOUT = int(os.environ.get("SANDBOX_TIMEOUT", "180"))


@pytest.fixture
def provider_config():
    """KubernetesProviderConfig for integration tests (tunnel mode)."""
    from langchain_kubernetes.config import KubernetesProviderConfig

    return KubernetesProviderConfig(
        template_name=SANDBOX_TEMPLATE,
        namespace=SANDBOX_NAMESPACE,
        connection_mode="tunnel",
        startup_timeout_seconds=SANDBOX_TIMEOUT,
    )


@pytest.fixture
def provider(provider_config):
    """KubernetesProvider for integration tests."""
    from langchain_kubernetes.provider import KubernetesProvider

    return KubernetesProvider(provider_config)
