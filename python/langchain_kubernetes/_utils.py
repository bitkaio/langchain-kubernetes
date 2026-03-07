"""Utility helpers: label constants, ID generation, polling."""

from __future__ import annotations

import secrets
import time
from typing import Callable

# Standard Kubernetes labels applied to all managed resources
LABEL_MANAGED_BY = "app.kubernetes.io/managed-by"
LABEL_COMPONENT = "app.kubernetes.io/component"
LABEL_SANDBOX_ID = "deepagents.langchain.com/sandbox-id"

MANAGED_BY_VALUE = "deepagents"
COMPONENT_VALUE = "sandbox"

# Label selector used when listing all deepagents sandboxes
MANAGED_BY_SELECTOR = f"{LABEL_MANAGED_BY}={MANAGED_BY_VALUE},{LABEL_COMPONENT}={COMPONENT_VALUE}"


def make_sandbox_id() -> str:
    """Generate a short random hex ID for a new sandbox.

    Returns:
        Eight-character lowercase hex string, e.g. ``"a1b2c3d4"``.
    """
    return secrets.token_hex(4)


def make_pod_name(sandbox_id: str) -> str:
    """Build the Pod name from a sandbox ID.

    Args:
        sandbox_id: Short hex sandbox identifier.

    Returns:
        Pod name string, e.g. ``"deepagents-sandbox-a1b2c3d4"``.
    """
    return f"deepagents-sandbox-{sandbox_id}"


def make_namespace_name(sandbox_id: str) -> str:
    """Build a per-sandbox namespace name.

    Args:
        sandbox_id: Short hex sandbox identifier.

    Returns:
        Namespace name, e.g. ``"deepagents-sandbox-a1b2c3d4"``.
    """
    return f"deepagents-sandbox-{sandbox_id}"


def common_labels(sandbox_id: str) -> dict[str, str]:
    """Return the standard label set for a given sandbox ID.

    Args:
        sandbox_id: Sandbox identifier to embed in labels.

    Returns:
        Dict of Kubernetes labels.
    """
    return {
        LABEL_MANAGED_BY: MANAGED_BY_VALUE,
        LABEL_COMPONENT: COMPONENT_VALUE,
        LABEL_SANDBOX_ID: sandbox_id,
    }


def poll_until(
    condition: Callable[[], bool],
    *,
    timeout: float,
    interval: float = 2.0,
    on_timeout: Callable[[], None] | None = None,
) -> None:
    """Poll *condition* every *interval* seconds until it returns ``True``.

    Args:
        condition: Callable returning ``True`` when the desired state is reached.
        timeout: Maximum number of seconds to wait.
        interval: Sleep duration between polls (default 2 s).
        on_timeout: Optional cleanup callable invoked before raising on timeout.

    Raises:
        TimeoutError: If *condition* never becomes ``True`` within *timeout*.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(interval)
    if on_timeout:
        on_timeout()
    raise TimeoutError(f"Timed out after {timeout:.0f}s waiting for condition")
