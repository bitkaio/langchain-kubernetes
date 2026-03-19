"""Label and annotation constants and utilities for langchain-kubernetes resources.

All provider-managed resources carry the prefix ``langchain-kubernetes.bitkaio.com/``
on every label and annotation to avoid collisions with cluster-level or
application-level labels.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Label / annotation keys
# ---------------------------------------------------------------------------

#: Namespace prefix applied to all labels and annotations.
LABEL_PREFIX = "langchain-kubernetes.bitkaio.com/"

#: Label key: identifies resources managed by this provider.
LABEL_MANAGED_BY = f"{LABEL_PREFIX}managed-by"

#: Label value for :data:`LABEL_MANAGED_BY`.
LABEL_MANAGED_BY_VALUE = "langchain-kubernetes"

#: Label key: identifies the thread (conversation) that owns this sandbox.
LABEL_THREAD_ID = f"{LABEL_PREFIX}thread-id"

#: Label key: warm-pool lifecycle status (``"warm"`` | ``"active"``).
LABEL_POOL_STATUS = f"{LABEL_PREFIX}pool-status"

#: Annotation key: original thread_id before sanitisation (only set when hashed).
ANN_THREAD_ID_ORIGINAL = f"{LABEL_PREFIX}thread-id-original"

#: Annotation key: absolute TTL from creation, in seconds.
ANN_TTL_SECONDS = f"{LABEL_PREFIX}ttl-seconds"

#: Annotation key: idle TTL from last :meth:`execute` call, in seconds.
ANN_TTL_IDLE_SECONDS = f"{LABEL_PREFIX}ttl-idle-seconds"

#: Annotation key: ISO-8601 UTC timestamp when the sandbox was created.
ANN_CREATED_AT = f"{LABEL_PREFIX}created-at"

#: Annotation key: ISO-8601 UTC timestamp of the last :meth:`execute` call.
ANN_LAST_ACTIVITY = f"{LABEL_PREFIX}last-activity"

#: Warm-pool pod status values.
POOL_STATUS_WARM = "warm"
POOL_STATUS_ACTIVE = "active"

#: Label selector matching all sandboxes managed by this provider.
MANAGED_SELECTOR = f"{LABEL_MANAGED_BY}={LABEL_MANAGED_BY_VALUE}"

# ---------------------------------------------------------------------------
# Validation regex for K8s label values
# ---------------------------------------------------------------------------

# Kubernetes label values must be empty OR:
#   - ≤ 63 characters
#   - consist of alphanumeric characters, '-', '_', or '.'
#   - start and end with an alphanumeric character
_VALID_LABEL_RE = re.compile(
    r'^[a-zA-Z0-9]([a-zA-Z0-9._-]{0,61}[a-zA-Z0-9])?$|^[a-zA-Z0-9]$|^$'
)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def sanitize_label_value(value: str) -> tuple[str, str | None]:
    """Sanitize *value* for use as a Kubernetes label value.

    Kubernetes label values must be ≤ 63 characters and match
    ``[a-zA-Z0-9._-]``, starting and ending with alphanumeric characters.

    If *value* fails these constraints it is hashed (SHA-256, first 12 hex
    chars) and the original is returned so the caller can store it as an
    annotation.

    Args:
        value: Raw string to sanitize.

    Returns:
        Tuple of ``(safe_value, original_if_hashed_else_None)``.
    """
    if value == "" or (len(value) <= 63 and _VALID_LABEL_RE.match(value)):
        return value, None
    hashed = hashlib.sha256(value.encode()).hexdigest()[:12]
    return hashed, value


def build_labels(
    *,
    default_labels: dict[str, str] | None = None,
    call_labels: dict[str, str] | None = None,
    thread_id: str | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Merge labels following the priority order defined in the spec.

    Priority (later wins):

    1. ``managed-by`` (always set).
    2. *default_labels* — config-level labels, auto-prefixed.
    3. *call_labels* — per-call labels, auto-prefixed, override defaults.
    4. ``thread-id`` — if *thread_id* is provided.

    User-supplied keys in *default_labels* and *call_labels* are automatically
    prefixed with :data:`LABEL_PREFIX`.

    Args:
        default_labels: Config-level user labels. Keys are auto-prefixed.
        call_labels: Per-call user labels. Keys are auto-prefixed.
        thread_id: Thread identifier to encode as a label.

    Returns:
        Tuple of ``(labels_dict, annotations_dict)``.  *annotations_dict* is
        non-empty only when *thread_id* required sanitisation.
    """
    labels: dict[str, str] = {LABEL_MANAGED_BY: LABEL_MANAGED_BY_VALUE}
    annotations: dict[str, str] = {}

    for source in (default_labels, call_labels):
        if source:
            for k, v in source.items():
                labels[f"{LABEL_PREFIX}{k}"] = v

    if thread_id is not None:
        safe, original = sanitize_label_value(thread_id)
        labels[LABEL_THREAD_ID] = safe
        if original is not None:
            annotations[ANN_THREAD_ID_ORIGINAL] = original

    return labels, annotations


def build_ttl_annotations(
    *,
    ttl_seconds: int | None = None,
    ttl_idle_seconds: int | None = None,
) -> dict[str, str]:
    """Build TTL-related annotation dicts.

    Args:
        ttl_seconds: Absolute TTL from creation (seconds).
        ttl_idle_seconds: Idle TTL from last execute() (seconds).

    Returns:
        Dict of annotation key → value pairs.
    """
    annotations: dict[str, str] = {}
    if ttl_seconds is not None:
        annotations[ANN_TTL_SECONDS] = str(ttl_seconds)
        annotations[ANN_CREATED_AT] = now_iso()
    if ttl_idle_seconds is not None:
        annotations[ANN_TTL_IDLE_SECONDS] = str(ttl_idle_seconds)
    return annotations


def now_iso() -> str:
    """Return the current UTC timestamp in ISO-8601 format.

    Returns:
        String suitable for use in K8s annotations, e.g.
        ``"2026-03-19T10:00:00+00:00"``.
    """
    return datetime.now(timezone.utc).isoformat()


def thread_id_selector(thread_id: str) -> str:
    """Build a label selector string for a specific *thread_id*.

    Args:
        thread_id: Raw thread identifier (will be sanitised automatically).

    Returns:
        Label selector string suitable for the K8s ``labelSelector``
        query-param.
    """
    safe, _ = sanitize_label_value(thread_id)
    return f"{LABEL_MANAGED_BY}={LABEL_MANAGED_BY_VALUE},{LABEL_THREAD_ID}={safe}"


def warm_pool_selector() -> str:
    """Return the label selector for available warm-pool Pods.

    Returns:
        Label selector string.
    """
    return f"{LABEL_MANAGED_BY}={LABEL_MANAGED_BY_VALUE},{LABEL_POOL_STATUS}={POOL_STATUS_WARM}"
