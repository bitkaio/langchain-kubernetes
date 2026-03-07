"""Kubernetes manifest builders for sandbox Pods, Namespaces, and NetworkPolicies."""

from __future__ import annotations

from typing import Any

from langchain_kubernetes._utils import common_labels
from langchain_kubernetes.config import KubernetesProviderConfig


def build_namespace_manifest(name: str, sandbox_id: str) -> dict[str, Any]:
    """Return a Namespace manifest dict.

    Args:
        name: Namespace name.
        sandbox_id: Sandbox identifier to embed in labels.

    Returns:
        Kubernetes Namespace manifest as a plain Python dict.
    """
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": name,
            "labels": common_labels(sandbox_id),
        },
    }


def build_pod_manifest(
    *,
    pod_name: str,
    namespace: str,
    sandbox_id: str,
    config: KubernetesProviderConfig,
) -> dict[str, Any]:
    """Return a Pod manifest dict that runs ``sleep infinity``.

    The Pod is configured with secure defaults: non-root user, all Linux
    capabilities dropped, no privilege escalation, RuntimeDefault seccomp
    profile, and no automounted service-account token.

    Args:
        pod_name: Name for the Pod resource.
        namespace: Namespace in which to create the Pod.
        sandbox_id: Sandbox identifier embedded in labels.
        config: Provider configuration driving image, resources, env vars, etc.

    Returns:
        Kubernetes Pod manifest as a plain Python dict.
    """
    labels = common_labels(sandbox_id)

    # Build resource requirements (omit keys whose values are None)
    resources: dict[str, Any] = {}
    requests: dict[str, str] = {}
    limits: dict[str, str] = {}
    if config.cpu_request:
        requests["cpu"] = config.cpu_request
    if config.memory_request:
        requests["memory"] = config.memory_request
    if config.cpu_limit:
        limits["cpu"] = config.cpu_limit
    if config.memory_limit:
        limits["memory"] = config.memory_limit
    if requests:
        resources["requests"] = requests
    if limits:
        resources["limits"] = limits

    # Build environment variables
    env = [{"name": k, "value": v} for k, v in config.extra_env.items()]

    security_context: dict[str, Any] = {
        "allowPrivilegeEscalation": False,
        "runAsNonRoot": True,
        "capabilities": {"drop": ["ALL"]},
    }
    if config.run_as_user is not None:
        security_context["runAsUser"] = config.run_as_user
    if config.run_as_group is not None:
        security_context["runAsGroup"] = config.run_as_group
    if config.seccomp_profile is not None:
        security_context["seccompProfile"] = {"type": config.seccomp_profile}

    container: dict[str, Any] = {
        "name": config.container_name,
        "image": config.image,
        "imagePullPolicy": config.image_pull_policy,
        "command": ["sleep", "infinity"],
        "securityContext": security_context,
    }
    if resources:
        container["resources"] = resources
    if env:
        container["env"] = env

    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name,
            "namespace": namespace,
            "labels": labels,
        },
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "containers": [container],
        },
    }


def build_network_policy_manifest(
    *,
    namespace: str,
    sandbox_id: str,
) -> dict[str, Any]:
    """Return a deny-all NetworkPolicy manifest for a sandbox Pod.

    The policy matches Pods by the ``deepagents.langchain.com/sandbox-id``
    label and blocks all ingress and egress traffic.

    Args:
        namespace: Namespace where the NetworkPolicy will live.
        sandbox_id: Sandbox identifier used for both the name suffix and
            the pod-selector label.

    Returns:
        Kubernetes NetworkPolicy manifest as a plain Python dict.
    """
    from langchain_kubernetes._utils import LABEL_SANDBOX_ID

    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": f"deepagents-sandbox-deny-all-{sandbox_id}",
            "namespace": namespace,
            "labels": common_labels(sandbox_id),
        },
        "spec": {
            "podSelector": {
                "matchLabels": {LABEL_SANDBOX_ID: sandbox_id},
            },
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [],
            "egress": [],
        },
    }
