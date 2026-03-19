"""Shared dataclasses for provider-level operations."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SandboxInfo:
    """Metadata about a single sandbox returned by :meth:`~KubernetesProvider.list`.

    Attributes:
        id: Sandbox identifier (Pod name for raw mode, claim name for
            agent-sandbox mode).
        namespace: Kubernetes namespace.
        thread_id: Thread identifier, extracted from the
            ``langchain-kubernetes.bitkaio.com/thread-id`` label, if present.
        labels: All labels attached to the Kubernetes resource.
        annotations: All annotations attached to the Kubernetes resource.
        created_at: ISO-8601 creation timestamp from the
            ``langchain-kubernetes.bitkaio.com/created-at`` annotation.
        last_activity: ISO-8601 timestamp of the last execute() call, from the
            ``langchain-kubernetes.bitkaio.com/last-activity`` annotation.
        status: Human-readable status string: ``"running"``, ``"warm"``,
            ``"pending"``, or ``"terminated"``.
    """

    id: str
    namespace: str
    thread_id: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    annotations: dict[str, str] = field(default_factory=dict)
    created_at: str | None = None
    last_activity: str | None = None
    status: str | None = None


@dataclass
class SandboxListResponse:
    """Paginated response from :meth:`~KubernetesProvider.list`.

    Attributes:
        sandboxes: List of sandbox metadata entries.
        cursor: Kubernetes ``continue`` token for the next page. ``None`` if
            this is the last (or only) page.
    """

    sandboxes: list[SandboxInfo]
    cursor: str | None = None


@dataclass
class CleanupResult:
    """Result of a :meth:`~KubernetesProvider.cleanup` operation.

    Attributes:
        deleted: Sandbox IDs that were deleted.
        kept: Number of sandboxes that were within their TTL / idle threshold.
    """

    deleted: list[str] = field(default_factory=list)
    kept: int = 0


@dataclass
class WarmPoolStatus:
    """Current state of the raw-mode warm pool.

    Attributes:
        available: Number of warm Pods ready to be claimed.
        active: Number of Pods currently assigned to a thread.
        total: ``available + active``.
        target: Configured ``warm_pool_size``.
    """

    available: int
    active: int
    total: int
    target: int


@dataclass
class ProviderStats:
    """Aggregate statistics returned by :meth:`~KubernetesProvider.stats`.

    Attributes:
        total: Total number of managed sandboxes.
        running: Number in ``Running`` phase (or Ready in agent-sandbox mode).
        warm: Number of warm-pool Pods (raw mode only).
        idle: Number of running sandboxes whose last-activity is older than the
            idle threshold (default 300 s).
        thread_ids: Count of distinct thread IDs across all sandboxes.
    """

    total: int
    running: int
    warm: int
    idle: int
    thread_ids: int
