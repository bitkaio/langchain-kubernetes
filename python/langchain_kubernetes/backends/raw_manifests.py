"""Pure functions that build Kubernetes manifest dicts for raw mode.

All functions return plain ``dict`` values suitable for passing directly to
the Kubernetes Python client API methods.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_kubernetes.config import KubernetesProviderConfig

# ---------------------------------------------------------------------------
# Standard label keys and values
# ---------------------------------------------------------------------------

LABEL_MANAGED_BY = "app.kubernetes.io/managed-by"
LABEL_COMPONENT = "app.kubernetes.io/component"
LABEL_SANDBOX_ID = "deepagents.langchain.com/sandbox-id"

_MANAGED_BY_VALUE = "deepagents"
_COMPONENT_VALUE = "sandbox"


def sandbox_labels(sandbox_id: str) -> dict[str, str]:
    """Return the standard set of labels for a sandbox resource.

    Args:
        sandbox_id: Unique sandbox identifier.

    Returns:
        Dict of label key→value pairs.
    """
    return {
        LABEL_MANAGED_BY: _MANAGED_BY_VALUE,
        LABEL_COMPONENT: _COMPONENT_VALUE,
        LABEL_SANDBOX_ID: sandbox_id,
    }


# ---------------------------------------------------------------------------
# Manifest builders
# ---------------------------------------------------------------------------


def build_pod_manifest(config: "KubernetesProviderConfig", sandbox_id: str) -> dict:
    """Build a Pod manifest for a raw-mode sandbox.

    The Pod runs ``["sleep", "infinity"]`` by default. All work happens through
    the Kubernetes exec API — no long-running process is expected.

    Security defaults (all enforced unconditionally):

    - ``runAsNonRoot: true``
    - ``allowPrivilegeEscalation: false``
    - ``capabilities.drop: ["ALL"]``
    - ``seccompProfile.type`` from config (default ``"RuntimeDefault"``)
    - ``automountServiceAccountToken: false``

    Args:
        config: Provider configuration supplying image, resources, and security settings.
        sandbox_id: Unique sandbox identifier (used in labels and Pod name).

    Returns:
        Pod manifest dict.
    """
    labels = sandbox_labels(sandbox_id)
    annotations = dict(config.extra_annotations)

    container: dict = {
        "name": "sandbox",
        "image": config.image,
        "imagePullPolicy": config.image_pull_policy,
        "command": list(config.command),
        "workingDir": config.workdir,
        "securityContext": {
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
            "seccompProfile": {"type": config.seccomp_profile},
        },
        "resources": {
            "requests": {
                "cpu": config.cpu_request,
                "memory": config.memory_request,
            },
            "limits": {
                "cpu": config.cpu_limit,
                "memory": config.memory_limit,
                "ephemeral-storage": config.ephemeral_storage_limit,
            },
        },
    }

    if config.env:
        container["env"] = [{"name": k, "value": v} for k, v in config.env.items()]

    if config.volume_mounts:
        container["volumeMounts"] = list(config.volume_mounts)

    pod_spec: dict = {
        "restartPolicy": "Never",
        "automountServiceAccountToken": False,
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": config.run_as_user,
            "runAsGroup": config.run_as_group,
        },
        "containers": [container],
    }

    if config.service_account:
        pod_spec["serviceAccountName"] = config.service_account

    if config.node_selector:
        pod_spec["nodeSelector"] = dict(config.node_selector)

    if config.tolerations:
        pod_spec["tolerations"] = list(config.tolerations)

    if config.volumes:
        pod_spec["volumes"] = list(config.volumes)

    if config.init_containers:
        pod_spec["initContainers"] = list(config.init_containers)

    if config.image_pull_secrets:
        pod_spec["imagePullSecrets"] = [{"name": s} for s in config.image_pull_secrets]

    manifest: dict = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": f"deepagents-{sandbox_id}",
            "labels": labels,
            "annotations": annotations,
        },
        "spec": pod_spec,
    }

    if config.pod_template_overrides:
        _deep_merge(manifest["spec"], config.pod_template_overrides)

    return manifest


def build_namespace_manifest(name: str, labels: dict[str, str] | None = None) -> dict:
    """Build a Namespace manifest for per-sandbox isolation.

    Args:
        name: Namespace name (e.g. ``"deepagents-abc12345"``).
        labels: Additional labels to merge with the standard managed-by labels.

    Returns:
        Namespace manifest dict.
    """
    return {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": name,
            "labels": {
                LABEL_MANAGED_BY: _MANAGED_BY_VALUE,
                LABEL_COMPONENT: _COMPONENT_VALUE,
                **(labels or {}),
            },
        },
    }


def build_network_policy_manifest(sandbox_id: str, namespace: str) -> dict:
    """Build a deny-all NetworkPolicy for the sandbox Pod.

    Blocks **all** ingress and egress traffic to and from the sandbox Pod.
    The policy uses a ``podSelector`` targeting the sandbox-specific label so
    it only affects the sandbox Pod even when not using a per-sandbox namespace.

    Args:
        sandbox_id: Unique sandbox identifier.
        namespace: Namespace where the policy will be created.

    Returns:
        NetworkPolicy manifest dict.
    """
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": f"deepagents-deny-all-{sandbox_id}",
            "namespace": namespace,
            "labels": sandbox_labels(sandbox_id),
        },
        "spec": {
            "podSelector": {
                "matchLabels": {
                    LABEL_SANDBOX_ID: sandbox_id,
                }
            },
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [],
            "egress": [],
        },
    }


def build_resource_quota_manifest(namespace: str, config: "KubernetesProviderConfig") -> dict:
    """Build a ResourceQuota for a per-sandbox namespace.

    Limits CPU and memory consumption in the namespace to the values configured
    for the sandbox.

    Args:
        namespace: Namespace to which the quota applies.
        config: Provider configuration supplying resource limits.

    Returns:
        ResourceQuota manifest dict.
    """
    return {
        "apiVersion": "v1",
        "kind": "ResourceQuota",
        "metadata": {
            "name": "deepagents-quota",
            "namespace": namespace,
            "labels": {
                LABEL_MANAGED_BY: _MANAGED_BY_VALUE,
                LABEL_COMPONENT: _COMPONENT_VALUE,
            },
        },
        "spec": {
            "hard": {
                "requests.cpu": config.cpu_request,
                "limits.cpu": config.cpu_limit,
                "requests.memory": config.memory_request,
                "limits.memory": config.memory_limit,
            }
        },
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _deep_merge(base: dict, override: dict) -> None:
    """Recursively merge *override* into *base* in-place.

    Nested dicts are merged recursively; all other types overwrite.

    Args:
        base: Target dict to merge into (modified in-place).
        override: Dict of values to merge from.
    """
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
