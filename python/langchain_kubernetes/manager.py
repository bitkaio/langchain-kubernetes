"""KubernetesSandboxManager: LangGraph-integrated sandbox lifecycle manager.

Provides a ``create_agent_node()`` factory that generates a LangGraph node
function managing the full sandbox acquire-or-reconnect cycle. All sandbox
state is stored in the graph state dict and persisted by LangGraph's
checkpointer — no Kubernetes label writes or direct cluster API access required.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Callable

from langchain_kubernetes.config import KubernetesProviderConfig
from langchain_kubernetes.provider import KubernetesProvider
from langchain_kubernetes.sandbox import KubernetesSandbox

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Key used to store sandbox ID in LangGraph graph state by default.
DEFAULT_SANDBOX_STATE_KEY = "sandbox_id"


class KubernetesSandboxManager:
    """High-level sandbox manager for LangGraph applications.

    Wraps a stateless :class:`~langchain_kubernetes.provider.KubernetesProvider`
    and provides :meth:`create_agent_node` — a factory that returns a LangGraph
    node function handling the complete sandbox lifecycle:

    1. Read ``sandbox_id`` from graph state (``None`` on first run).
    2. Reconnect to the existing sandbox if ``sandbox_id`` is set and the
       sandbox is still alive.
    3. Provision a new sandbox if none exists or the previous one expired.
    4. Run the DeepAgents agent with the sandbox for this invocation.
    5. Write the (possibly new) ``sandbox_id`` back to graph state so
       LangGraph's checkpointer persists it for the next run.

    **No state is held in memory.** The ``sandbox_id`` lives exclusively in
    the LangGraph graph state and is persisted by whichever checkpointer the
    user configures (``MemorySaver``, ``PostgresSaver``, ``RedisSaver``, …).
    This means the integration works transparently across process restarts,
    horizontal scaling, and the LangGraph Platform without any Kubernetes
    label machinery.

    Example — minimal LangGraph integration::

        from langgraph.graph import StateGraph, END
        from langchain_kubernetes import KubernetesProviderConfig
        from langchain_kubernetes.manager import KubernetesSandboxManager
        from typing import TypedDict, Annotated
        from langchain_core.messages import AnyMessage
        from langgraph.graph.message import add_messages

        class AgentState(TypedDict):
            messages: Annotated[list[AnyMessage], add_messages]
            sandbox_id: str | None  # persisted by LangGraph checkpointer

        manager = KubernetesSandboxManager(
            KubernetesProviderConfig(
                mode="agent-sandbox",
                template_name="python-sandbox-template",
                connection_mode="gateway",
                gateway_name="my-gateway",
            )
        )

        builder = StateGraph(AgentState)
        builder.add_node("agent", manager.create_agent_node(model))
        builder.set_entry_point("agent")
        builder.add_edge("agent", END)
        graph = builder.compile(checkpointer=MemorySaver())

    Args:
        provider_config: Configuration for the underlying
            :class:`~langchain_kubernetes.provider.KubernetesProvider`.
        ttl_seconds: Absolute TTL applied to newly created sandboxes.
        ttl_idle_seconds: Idle TTL applied to newly created sandboxes.
        default_labels: Labels applied to every provisioned sandbox.
    """

    def __init__(
        self,
        provider_config: KubernetesProviderConfig,
        ttl_seconds: int | None = None,
        ttl_idle_seconds: int | None = None,
        default_labels: dict[str, str] | None = None,
    ) -> None:
        self._provider = KubernetesProvider(provider_config)
        self._ttl_seconds = ttl_seconds
        self._ttl_idle_seconds = ttl_idle_seconds
        self._default_labels = default_labels
        self._sandbox_by_thread: dict[str, KubernetesSandbox] = {}

    # ------------------------------------------------------------------
    # Primary integration point: LangGraph node factory
    # ------------------------------------------------------------------

    def _make_backend_factory(self) -> Callable:
        """Return a sync backend factory that reads the cached sandbox for the current thread.

        Safe to call from deepagents' synchronous middleware — does a plain dict
        lookup, no I/O. The sandbox must have been placed in the cache by the
        setup node before the deepagent subgraph runs.
        """
        manager = self

        def factory(runtime: Any) -> "KubernetesSandbox":
            from langchain_core.runnables.config import ensure_config

            config = ensure_config()
            thread_id: str | None = (config.get("configurable") or {}).get("thread_id")
            if thread_id is None:
                raise RuntimeError(
                    "KubernetesSandboxManager: no thread_id in LangGraph config. "
                    "Invoke with config={'configurable': {'thread_id': '...'}}."
                )
            sandbox = manager._sandbox_by_thread.get(thread_id)
            if sandbox is None:
                raise RuntimeError(
                    f"KubernetesSandboxManager: no sandbox cached for thread "
                    f"{thread_id!r}. Ensure the setup node runs before the agent."
                )
            return sandbox

        return factory

    def create_setup_node(
        self,
        *,
        state_sandbox_key: str = DEFAULT_SANDBOX_STATE_KEY,
    ) -> Callable:
        """Return an async LangGraph node that acquires the sandbox and caches it.

        Reads ``state[state_sandbox_key]`` to reconnect an existing sandbox (or
        provision a new one), stores it in ``_sandbox_by_thread[thread_id]``, and
        writes the (possibly new) ``sandbox_id`` back to state.

        Must be wired to run before the deepagent subgraph node.

        Args:
            state_sandbox_key: State field that holds the sandbox ID.

        Returns:
            An ``async def setup_node(state, config)`` function.
        """
        manager = self

        async def setup_node(state: dict[str, Any], config: Any = None) -> dict[str, Any]:
            from langchain_core.runnables.config import ensure_config

            cfg = config or ensure_config()
            thread_id: str | None = (cfg.get("configurable") or {}).get("thread_id")
            if thread_id is None:
                raise RuntimeError(
                    "KubernetesSandboxManager setup_node: no thread_id in config."
                )
            sandbox_id: str | None = state.get(state_sandbox_key)
            sandbox = await manager._aget_or_reconnect(sandbox_id)
            manager._sandbox_by_thread[thread_id] = sandbox
            updates: dict[str, Any] = {}
            if sandbox.id != sandbox_id:
                updates[state_sandbox_key] = sandbox.id
            return updates

        setup_node.__name__ = "setup_node"
        return setup_node

    def create_agent(
        self,
        model: Any,
        *,
        checkpointer: Any = None,
        state_sandbox_key: str = DEFAULT_SANDBOX_STATE_KEY,
        **create_deep_agent_kwargs: Any,
    ) -> Any:
        """Return a compiled LangGraph graph with streaming-compatible sandbox management.

        Uses a two-node architecture so the deepagent runs as a proper LangGraph
        subgraph, enabling real-time streaming of LLM tokens and tool calls::

            START → setup (acquire sandbox) → agent (deepagent subgraph) → END

        ``sandbox_id`` is persisted in graph state and handled by the checkpointer.

        Args:
            model: A LangChain ``BaseChatModel`` passed to ``create_deep_agent()``.
            checkpointer: LangGraph checkpointer (``MemorySaver``, ``PostgresSaver``,
                …). Required for multi-turn conversations when calling ``.invoke()``
                directly (e.g. in FastAPI). The LangGraph Platform / ``langgraph dev``
                provide their own checkpointer — pass ``None`` there.
            state_sandbox_key: State field name for the sandbox ID. Defaults to
                ``"sandbox_id"``.
            **create_deep_agent_kwargs: Extra options forwarded to
                ``create_deep_agent()`` (e.g. ``system_prompt``, ``tools``).

        Returns:
            Compiled ``StateGraph`` with ``"setup"`` and ``"agent"`` nodes.

        Example::

            manager = KubernetesSandboxManager(config)

            # For direct invocation (FastAPI, scripts)
            agent = manager.create_agent(llm, checkpointer=MemorySaver())
            result = agent.invoke(
                {"messages": [("user", "hello")]},
                config={"configurable": {"thread_id": "conv-1"}},
            )

            # For langgraph dev / LangGraph Platform — no checkpointer needed
            graph = manager.create_agent(llm)
        """
        from deepagents import create_deep_agent
        from langgraph.graph import END, START, StateGraph
        from typing import Annotated
        from typing_extensions import TypedDict
        from langchain_core.messages import AnyMessage
        from langgraph.graph.message import add_messages

        backend_factory = self._make_backend_factory()
        agent_subgraph = create_deep_agent(
            model, backend=backend_factory, **create_deep_agent_kwargs
        )

        class _AgentState(TypedDict):
            messages: Annotated[list[AnyMessage], add_messages]
            sandbox_id: str | None

        builder: StateGraph = StateGraph(_AgentState)
        builder.add_node("setup", self.create_setup_node(state_sandbox_key=state_sandbox_key))
        builder.add_node("agent", agent_subgraph)
        builder.add_edge(START, "setup")
        builder.add_edge("setup", "agent")
        builder.add_edge("agent", END)
        return builder.compile(checkpointer=checkpointer)

    def create_agent_node(
        self,
        model: Any,
        *,
        state_sandbox_key: str = DEFAULT_SANDBOX_STATE_KEY,
        **create_deep_agent_kwargs: Any,
    ) -> Callable:
        """Return an async LangGraph node function that manages the sandbox lifecycle.

        The returned node reads ``state[state_sandbox_key]`` to reconnect an
        existing sandbox or create a new one, runs the DeepAgents agent against
        the current messages, and returns updated messages plus the (possibly
        new) ``sandbox_id`` to be stored back in graph state.

        **Graph state requirements** — your ``TypedDict`` must include::

            messages: Annotated[list[AnyMessage], add_messages]
            sandbox_id: str | None   # or whatever state_sandbox_key you choose

        Args:
            model: A LangChain ``BaseChatModel`` (or compatible) passed to
                ``create_deep_agent()``.
            state_sandbox_key: Name of the graph-state field that holds the
                sandbox ID. Defaults to ``"sandbox_id"``.
            **create_deep_agent_kwargs: Extra keyword arguments forwarded to
                ``create_deep_agent()`` (e.g. ``tools``, ``system_prompt``).

        Returns:
            An ``async def agent_node(state, config)`` function suitable for
            ``StateGraph.add_node()``.

        Example::

            builder.add_node("agent", manager.create_agent_node(
                model,
                system_prompt="You are a helpful data analyst.",
            ))
        """
        manager = self  # capture for closure

        async def agent_node(state: dict[str, Any], config: Any = None) -> dict[str, Any]:
            from deepagents import create_deep_agent

            sandbox_id: str | None = state.get(state_sandbox_key)

            # Acquire sandbox — reconnects if sandbox_id is valid, creates otherwise
            sandbox = await manager._aget_or_reconnect(sandbox_id)

            # Build a fresh agent bound to this sandbox for the current invocation
            agent = create_deep_agent(model, backend=sandbox, **create_deep_agent_kwargs)

            messages = state.get("messages", [])
            result = await agent.ainvoke({"messages": messages}, config or {})

            updates: dict[str, Any] = {"messages": result.get("messages", [])}

            # Persist sandbox_id if it changed (new sandbox was provisioned)
            if sandbox.id != sandbox_id:
                updates[state_sandbox_key] = sandbox.id

            return updates

        agent_node.__name__ = "agent_node"
        return agent_node

    # ------------------------------------------------------------------
    # Lower-level helper: acquire sandbox from state
    # ------------------------------------------------------------------

    async def _aget_or_reconnect(self, sandbox_id: str | None) -> KubernetesSandbox:
        """Reconnect to *sandbox_id* if given and alive, else create a new sandbox.

        Args:
            sandbox_id: An existing sandbox ID from graph state, or ``None``.

        Returns:
            :class:`~langchain_kubernetes.sandbox.KubernetesSandbox`. Its ``.id``
            may differ from *sandbox_id* when a new sandbox was provisioned.
        """
        return await self._provider.aget_or_create(
            sandbox_id=sandbox_id,
            labels=self._default_labels,
            ttl_seconds=self._ttl_seconds,
            ttl_idle_seconds=self._ttl_idle_seconds,
        )

    # ------------------------------------------------------------------
    # Operational: cleanup / shutdown
    # ------------------------------------------------------------------

    def cleanup(self, max_idle_seconds: int | None = None) -> Any:
        """Delete all managed sandboxes that have exceeded their TTL.

        Delegates to :meth:`~KubernetesProvider.cleanup`, which queries the
        Kubernetes API directly and removes expired resources.

        Args:
            max_idle_seconds: Override idle threshold for this call.

        Returns:
            :class:`~langchain_kubernetes._types.CleanupResult`.
        """
        return self._provider.cleanup(max_idle_seconds)

    async def acleanup(self, max_idle_seconds: int | None = None) -> Any:
        """Async wrapper around :meth:`cleanup`."""
        return await self._provider.acleanup(max_idle_seconds)

    def shutdown(self) -> None:
        """Delete all sandboxes managed by this provider instance.

        Runs :meth:`cleanup` without an idle threshold, removing all managed
        sandboxes regardless of their remaining TTL. Useful for tearing down
        a dev environment or a batch job on exit.

        Errors during individual deletions are logged but not re-raised.
        """
        try:
            result = self._provider.cleanup()
            if result.deleted:
                logger.info("Shutdown: deleted sandboxes %s", result.deleted)
        except Exception as exc:
            logger.warning("Shutdown: cleanup failed: %s", exc)

    async def ashutdown(self) -> None:
        """Async variant of :meth:`shutdown`."""
        await asyncio.to_thread(self.shutdown)

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "KubernetesSandboxManager":
        return self

    def __exit__(self, *args: Any) -> None:
        self.shutdown()

    async def __aenter__(self) -> "KubernetesSandboxManager":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.ashutdown()
