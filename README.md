# langchain-kubernetes

Kubernetes sandbox provider for the [DeepAgents](https://github.com/langchain-ai/deepagents) framework. Available for both Python and TypeScript.

Supports two backend modes:

- **`agent-sandbox` mode** (default, recommended) — integrates with [`kubernetes-sigs/agent-sandbox`](https://github.com/kubernetes-sigs/agent-sandbox). Requires the controller + CRDs installed in the cluster. Provides warm pools, gVisor/Kata isolation, and sub-second startup.
- **`raw` mode** (fallback) — directly manages ephemeral Pods via the Kubernetes API. Works on any cluster with no CRD installation. Uses tar-piped exec for file transfer and a deny-all NetworkPolicy for network isolation.

## Contents

- [Packages](#packages)
- [Quick start](#quick-start)
- [Use with DeepAgents](#use-with-deepagents)
  - [One-shot usage](#one-shot-usage)
  - [Per-thread sandbox management](#per-thread-sandbox-management-recommended-for-multi-user-apps)
  - [Automatic wiring with KubernetesSandboxManager](#automatic-per-thread-wiring-with-kubernetessandboxmanager)
  - [Hosting with langgraph dev / LangGraph Platform](#hosting-with-langgraph-dev--langgraph-platform-no-explicit-invoke)
  - [Operational management](#operational-management)
- [Per-conversation sandboxes](#per-conversation-sandboxes)
- [Warm pool — sub-second startup](#warm-pool--sub-second-startup)
- [For regulated industries](#for-regulated-industries)
- [Feature comparison](#feature-comparison)
- [Security defaults](#security-defaults)
- [Repository layout](#repository-layout)
- [Requirements](#requirements)

## Packages

| Package | Registry | Language |
| --- | --- | --- |
| `langchain-kubernetes` | PyPI | Python ≥ 3.11 |
| `langchain-kubernetes` | npm | Node.js ≥ 18 |

## Quick start

### Python

**agent-sandbox mode** (default — requires agent-sandbox controller in cluster):

```bash
pip install "langchain-kubernetes[agent-sandbox]"
```

```python
from langchain_kubernetes import KubernetesProvider, KubernetesProviderConfig

provider = KubernetesProvider(KubernetesProviderConfig(
    template_name="python-sandbox-template",
))
sandbox = provider.get_or_create()

try:
    result = sandbox.execute("python3 -c 'print(2 + 2)'")
    print(result.output)    # "4"
    print(result.exit_code) # 0
finally:
    provider.delete(sandbox_id=sandbox.id)
```

**raw mode** (fallback — works on any cluster, no CRDs required):

```bash
pip install "langchain-kubernetes[raw]"
```

```python
from langchain_kubernetes import KubernetesProvider, KubernetesProviderConfig

provider = KubernetesProvider(KubernetesProviderConfig(
    mode="raw",
    image="python:3.12-slim",
))
sandbox = provider.get_or_create()

try:
    result = sandbox.execute("python3 -c 'print(2 + 2)'")
    print(result.output)    # "4"
    print(result.exit_code) # 0
finally:
    provider.delete(sandbox_id=sandbox.id)
```

### TypeScript

```bash
npm install @bitkaio/langchain-kubernetes
```

**agent-sandbox mode** (default — requires agent-sandbox controller in cluster):

```typescript
import { KubernetesProvider } from "@bitkaio/langchain-kubernetes";

const provider = new KubernetesProvider({
  mode: "agent-sandbox",
  routerUrl: "http://sandbox-router-svc.default.svc.cluster.local:8080",
  templateName: "python-sandbox-template",
});
const sandbox = await provider.getOrCreate();

try {
    const result = await sandbox.execute("python3 -c 'print(42)'");
    console.log(result.output); // "42\n"
} finally {
    await provider.delete(sandbox.id);
}
```

**raw mode** (fallback — works on any cluster, requires `@kubernetes/client-node`):

```bash
npm install @bitkaio/langchain-kubernetes @kubernetes/client-node tar-stream
```

```typescript
import { KubernetesProvider } from "@bitkaio/langchain-kubernetes";

const provider = new KubernetesProvider({
  mode: "raw",
  image: "python:3.12-slim",
});
const sandbox = await provider.getOrCreate();

try {
    const result = await sandbox.execute("python3 -c 'print(42)'");
    console.log(result.output); // "42\n"
} finally {
    await provider.delete(sandbox.id);
}
```

## Use with DeepAgents

[DeepAgents](https://github.com/langchain-ai/deepagents) is LangChain's agent harness that gives agents a planning tool, filesystem backend, subagent spawning, and — when a sandbox backend is supplied — full shell execution via an `execute` tool. `KubernetesSandbox` implements `SandboxBackendProtocol`, so it plugs in directly.

### One-shot usage

Create a sandbox once, run one task, delete it:

```python
from langchain_anthropic import ChatAnthropic
from deepagents import create_deep_agent
from langchain_kubernetes import KubernetesProvider, KubernetesProviderConfig

provider = KubernetesProvider(KubernetesProviderConfig(
    template_name="python-sandbox-template",
))
sandbox = provider.get_or_create()

llm = ChatAnthropic(model="claude-opus-4-6")
agent = create_deep_agent(model=llm, backend=sandbox)

result = agent.invoke({
    "messages": [("user", "Write and run a Python script that prints the Fibonacci sequence")]
})
print(result["messages"][-1].content)

provider.delete(sandbox_id=sandbox.id)
```

### Per-thread sandbox management (recommended for multi-user apps)

When you serve many concurrent users you want **one persistent sandbox per conversation thread** — the sandbox survives between turns so installed packages, written files, and shell state are all retained. Use `get_or_create(thread_id=...)`:

```python
from langchain_anthropic import ChatAnthropic
from deepagents import create_deep_agent
from langchain_kubernetes import KubernetesProvider, KubernetesProviderConfig

provider = KubernetesProvider(KubernetesProviderConfig(
    template_name="python-sandbox-template",
    ttl_idle_seconds=1800,   # provider auto-deletes sandboxes idle > 30 min
))

llm = ChatAnthropic(model="claude-opus-4-6")

def handle_message(thread_id: str, user_message: str) -> str:
    # Idempotent: finds the existing sandbox for this thread if one exists,
    # creates a new one only on the first call. No try/except needed.
    sandbox = provider.get_or_create(thread_id=thread_id, ttl_seconds=86400)
    agent = create_deep_agent(model=llm, backend=sandbox)

    result = agent.invoke(
        {"messages": [("user", user_message)]},
        config={"configurable": {"thread_id": thread_id}},  # LangGraph checkpoint key
    )
    return result["messages"][-1].content

# Each call in the same thread reuses the same sandbox
print(handle_message("user-42-session-7", "Install numpy and create a random matrix"))
print(handle_message("user-42-session-7", "Print the matrix you just created"))  # state persists
```

> **Compare with alternative sandbox providers** that require manual lifecycle management:
>
> ```python
> # Other provider — verbose try/except with no warm pool, no label-based lookup
> try:
>     sandbox = client.find_one(labels={"thread_id": thread_id})
> except SandboxNotFound:
>     sandbox = client.create(CreateSandboxFromSnapshotParams(
>         labels={"thread_id": thread_id},
>         auto_delete_interval=3600,
>     ))
> backend = ProviderSandbox(sandbox=sandbox)
> agent = create_deep_agent(model=llm, backend=backend)
> ```
>
> `langchain-kubernetes` collapses the entire block to one line.

### Automatic per-thread wiring with `KubernetesSandboxManager`

For production deployments, `KubernetesSandboxManager` owns the full sandbox lifecycle and exposes a `backend_factory` callable that `create_deep_agent` accepts directly. The thread ID is extracted automatically from the LangGraph `RunnableConfig` — **no manual lookup code needed at all**:

```python
from langchain_anthropic import ChatAnthropic
from deepagents import create_deep_agent
from langchain_kubernetes import KubernetesSandboxManager, KubernetesProviderConfig

manager = KubernetesSandboxManager(
    KubernetesProviderConfig(
        template_name="python-sandbox-template",
        warm_pool_name="python-pool",   # agent-sandbox warm pool for instant startup
    ),
    ttl_seconds=86400,          # sandbox lives at most 24 h
    ttl_idle_seconds=1800,      # auto-deleted after 30 min of inactivity
    default_labels={"app": "my-chat-service"},
)

llm = ChatAnthropic(model="claude-opus-4-6")

# backend_factory is a Callable[[ToolRuntime], SandboxBackendProtocol].
# It extracts thread_id from the LangGraph config and calls get_or_create() internally.
agent = create_deep_agent(model=llm, backend=manager.backend_factory)

# Use with a LangGraph checkpointer for durable multi-turn conversations:
# from langgraph.checkpoint.memory import MemorySaver
# agent = create_deep_agent(model=llm, backend=manager.backend_factory,
#                           checkpointer=MemorySaver())

result = agent.invoke(
    {"messages": [("user", "Set up a data pipeline that downloads the iris dataset")]},
    config={"configurable": {"thread_id": "session-abc-123"}},
)
print(result["messages"][-1].content)

# Subsequent invocations with the same thread_id reuse the same sandbox,
# so files written in turn 1 are available in turn 2.
result2 = agent.invoke(
    {"messages": [("user", "Now plot the petal length distribution and save it as plot.png")]},
    config={"configurable": {"thread_id": "session-abc-123"}},
)

# Graceful shutdown: deletes all tracked sandboxes
manager.shutdown()
```

#### Async / FastAPI usage

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from langchain_anthropic import ChatAnthropic
from deepagents import create_deep_agent
from langchain_kubernetes import KubernetesSandboxManager, KubernetesProviderConfig

manager: KubernetesSandboxManager | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global manager
    manager = KubernetesSandboxManager(
        KubernetesProviderConfig(template_name="python-sandbox-template"),
        ttl_idle_seconds=1800,
    )
    yield
    await manager.ashutdown()   # async cleanup on shutdown

app = FastAPI(lifespan=lifespan)
llm = ChatAnthropic(model="claude-opus-4-6")

@app.post("/chat/{thread_id}")
async def chat(thread_id: str, message: str):
    agent = create_deep_agent(model=llm, backend=manager.backend_factory)
    result = await agent.ainvoke(
        {"messages": [("user", message)]},
        config={"configurable": {"thread_id": thread_id}},
    )
    return {"reply": result["messages"][-1].content}
```

#### TypeScript / Node.js

```typescript
import { ChatAnthropic } from "@langchain/anthropic";
import { createDeepAgent } from "deepagents";
import { KubernetesSandboxManager } from "@bitkaio/langchain-kubernetes";

const manager = new KubernetesSandboxManager(
  {
    templateName: "python-sandbox-template",
    routerUrl: "http://sandbox-router-svc.default.svc.cluster.local:8080",
    warmPoolName: "python-pool",
  },
  { ttlSeconds: 86400, ttlIdleSeconds: 1800 },
);

const llm = new ChatAnthropic({ model: "claude-opus-4-6" });

// backendFactory is (config: RunnableConfig) => Promise<KubernetesSandbox>
const agent = createDeepAgent({ model: llm, backend: manager.backendFactory });

const result = await agent.invoke(
  { messages: [{ role: "user", content: "Run a quick benchmark in Python" }] },
  { configurable: { threadId: "session-xyz-789" } },
);
console.log(result.messages.at(-1)?.content);

await manager.asyncShutdown();
```

### Hosting with `langgraph dev` / LangGraph Platform (no explicit `.invoke()`)

When you run your agent with the **LangGraph CLI** (`langgraph dev`) or deploy to LangGraph Platform, you never write `.invoke()` — the server handles that. You just define the graph in a module, point `langgraph.json` at it, and the server injects the `thread_id` for you.

The `KubernetesSandboxManager.backend_factory` pattern is *exactly* designed for this: the factory is called by DeepAgents on every turn with the current `RunnableConfig`, which already contains the server-assigned `thread_id`. Your code stays the same; only the entrypoint changes.

**`agent.py`** — define the graph once at module level, export it:

```python
from langchain_anthropic import ChatAnthropic
from deepagents import create_deep_agent
from langchain_kubernetes import KubernetesSandboxManager, KubernetesProviderConfig

# Module-level singletons — created once when the server imports this file,
# not recreated on every request.
manager = KubernetesSandboxManager(
    KubernetesProviderConfig(
        template_name="python-sandbox-template",
        warm_pool_name="python-pool",
    ),
    ttl_idle_seconds=1800,
    default_labels={"app": "my-langgraph-agent"},
)

llm = ChatAnthropic(model="claude-opus-4-6")

# The compiled graph is what langgraph.json points at.
# backend_factory is a callable — DeepAgents calls it on every turn and
# passes the current RunnableConfig, which already has thread_id injected
# by the LangGraph server.  No .invoke(), no manual thread_id wiring needed.
graph = create_deep_agent(model=llm, backend=manager.backend_factory)
```

**`langgraph.json`** — point the server at the graph variable:

```json
{
    "dependencies": ["."],
    "graphs": {
        "agent": "./agent.py:graph"
    }
}
```

Start the dev server (no Docker needed):

```bash
pip install "langgraph-cli[inmem]"
langgraph dev
# → Running on http://localhost:2024
# → LangGraph Studio: https://smith.langchain.com/studio/?baseUrl=http://localhost:2024
```

**Interacting with the running server** — use the LangGraph SDK or Studio UI:

```python
# Client code (e.g., your frontend / REST handler)
from langgraph_sdk import get_client

client = get_client(url="http://localhost:2024")

# Create a thread — the server assigns a persistent thread_id.
# This maps 1-to-1 to a KubernetesSandbox created on the first run.
thread = await client.threads.create()

# Run the agent. The server injects thread["thread_id"] into
# config["configurable"]["thread_id"] before calling the graph.
# manager.backend_factory sees it and calls provider.get_or_create(thread_id=...).
run = await client.runs.create(
    thread_id=thread["thread_id"],
    assistant_id="agent",
    input={"messages": [{"role": "user", "content": "Install pandas and summarise the iris dataset"}]},
)

# Second message in the same thread — reuses the same sandbox automatically.
run2 = await client.runs.create(
    thread_id=thread["thread_id"],
    assistant_id="agent",
    input={"messages": [{"role": "user", "content": "Now plot a histogram of petal length"}]},
)
```

> **Important:** if you use a **factory function** pattern instead of a compiled graph variable — where `langgraph.json` points to a function `make_graph(config)` that returns the graph — call `create_deep_agent` inside the factory:
>
> ```python
> def make_graph(config: dict):
>     # config["configurable"]["thread_id"] is already set here.
>     # You can use it to do per-graph customisation if needed,
>     # but backend_factory handles thread routing on its own.
>     return create_deep_agent(model=llm, backend=manager.backend_factory)
> ```
>
> ```json
> { "graphs": { "agent": "./agent.py:make_graph" } }
> ```
>
> Both patterns work. The compiled variable form is simpler; the factory form is useful when you need to vary the graph structure (tools, system prompt) per request.

### How thread_id flows through the stack

```text
── Direct .invoke() ──────────────────────────────────────────────────────────
agent.invoke(input, config={"configurable": {"thread_id": "abc"}})
  │
  └─▶ DeepAgents calls backend_factory(tool_runtime)          ─┐
                                                                │
── LangGraph server (langgraph dev / Platform) ─────────────── │ same path
POST /runs  { thread_id: "abc", input: {...} }                  │
  │                                                             │
  └─▶ LangGraph server injects thread_id into RunnableConfig   │
        └─▶ server calls graph(input, config)                   │
              └─▶ DeepAgents calls backend_factory(tool_runtime)─┘
                    │
                    └─▶ KubernetesSandboxManager extracts thread_id
                          │
                          └─▶ provider.get_or_create(thread_id="abc", ...)
                                │
                                ├─ (first run)   creates Pod/SandboxClaim
                                │                labels it thread-id=abc
                                │
                                └─ (later runs)  finds by label selector
                                                 reconnects → same sandbox
```

### Operational management

```python
# List all active sandboxes (optionally filter by label or thread)
response = provider.list(labels={"app": "my-chat-service"})
for sb in response.sandboxes:
    print(sb.id, sb.thread_id, sb.last_activity)

# Delete sandboxes that have been idle > 1 hour
result = provider.cleanup(max_idle_seconds=3600)
print(f"Deleted {result.deleted}, kept {result.kept}")

# Provider-wide stats
stats = provider.stats()
print(f"Total: {stats.total}, Running: {stats.running}, Warm: {stats.warm}")

# Warm pool status
pool = provider.pool_status()
print(f"Available: {pool.available}/{pool.total}")
```

## Per-conversation sandboxes

Assign a persistent sandbox to each conversation thread with a single call — the provider looks up an existing sandbox by thread ID before creating a new one:

```python
# Python — one line, idempotent
sandbox = provider.get_or_create(thread_id="conv-abc-123", ttl_seconds=3600)
```

```typescript
// TypeScript — same pattern
const sandbox = await provider.getOrCreate({ threadId: "conv-abc-123", ttlSeconds: 3600 });
```

Compare with alternative sandboxing libraries that require manual try/except lifecycle management:

```python
# Other provider (verbose)
try:
    sandbox = client.get(sandbox_id)
except SandboxNotFound:
    sandbox = client.create(image="python:3.12", timeout=30)
    client.update_metadata(sandbox.id, {"thread_id": "conv-abc-123"})
# No TTL, no warm pool, no label-based lookup
```

For LangGraph, use `KubernetesSandboxManager` — it wires directly into the `backend_factory` interface:

```python
from langchain_kubernetes import KubernetesSandboxManager, KubernetesProviderConfig

manager = KubernetesSandboxManager(
    KubernetesProviderConfig(template_name="python-sandbox-template"),
    ttl_idle_seconds=1800,   # auto-cleanup after 30 min of inactivity
)

# Pass to LangGraph — thread_id is extracted from RunnableConfig automatically
agent = create_deep_agent(model=llm, backend=manager.backend_factory)

# TypeScript equivalent
const manager = new KubernetesSandboxManager(
  { templateName: "python-sandbox-template", routerUrl: "..." },
  { ttlIdleSeconds: 1800 },
);
const factory = manager.backendFactory; // (config: RunnableConfig) => Promise<KubernetesSandbox>
```

## Warm pool — sub-second startup

Cold-starting a sandbox Pod takes 5–30 seconds. Warm pools keep pre-provisioned sandboxes ready for instant assignment.

**agent-sandbox mode** — managed by the `kubernetes-sigs/agent-sandbox` controller via a `SandboxWarmPool` CRD:

```yaml
apiVersion: extensions.agents.x-k8s.io/v1alpha1
kind: SandboxWarmPool
metadata:
  name: python-pool
spec:
  templateName: python-sandbox-template
  size: 5        # keep 5 sandboxes pre-warmed at all times
```

```python
provider = KubernetesProvider(KubernetesProviderConfig(
    template_name="python-sandbox-template",
    warm_pool_name="python-pool",   # claim from this pool
))
```

**raw mode** — built-in provider-managed warm pool. No CRDs, no controller:

```python
provider = KubernetesProvider(KubernetesProviderConfig(
    mode="raw",
    warm_pool_size=3,   # pre-create 3 idle Pods, replenish after each delete
))
```

This matters for chat applications: when a user sends their first message, the sandbox is already running. Startup latency is invisible to the user. See [`docs/warm-pool.yaml`](docs/warm-pool.yaml) for annotated examples.

## For regulated industries

All sandbox execution happens inside **your own cluster** — no data leaves your network:

- **On-prem / air-gapped**: works with any CNCF-conformant Kubernetes distribution including OpenShift, Rancher, and air-gapped kind clusters
- **Network isolation**: every sandbox Pod gets a deny-all `NetworkPolicy` — no internet access, no cross-sandbox traffic — enforced at the kernel networking layer
- **gVisor / Kata Containers**: use hardware-isolated runtimes in agent-sandbox mode by setting `runtimeClassName` in your `SandboxTemplate`
- **No data egress**: the sandbox-router and all execution traffic stay in-cluster; the SDK makes no external calls
- **Audit trail**: every sandbox carries `langchain-kubernetes.bitkaio.com/thread-id`, `created-at`, and `last-activity` annotations — queryable with `kubectl get pods --show-labels` or your existing observability stack

See [`docs/openshift.md`](docs/openshift.md) for OpenShift-specific notes (SCC, UID ranges, Routes).

## Feature comparison

| Feature                       | langchain-kubernetes | Daytona   | Modal | Runloop |
| ----------------------------- | -------------------- | --------- | ----- | ------- |
| Self-hosted / on-prem         | ✅                   | ❌        | ❌    | ❌      |
| Per-thread `get_or_create`    | ✅ built-in          | ✅ manual | ❌    | ❌      |
| LangGraph `backend_factory`   | ✅                   | ❌        | ❌    | ❌      |
| Warm pools                    | ✅                   | ❌        | ❌    | ❌      |
| gVisor / Kata isolation       | ✅                   | ❌        | ❌    | ❌      |
| Network policies per sandbox  | ✅                   | ❌        | ❌    | ❌      |
| TTL auto-cleanup              | ✅                   | ✅        | ✅    | ❌      |

## Security defaults

Every Pod is created with hardened defaults:

- `runAsNonRoot: true`
- `runAsUser: 1000` / `runAsGroup: 1000` (configurable, set to `None`/`undefined` for OpenShift)
- `allowPrivilegeEscalation: false`
- `capabilities.drop: [ALL]`
- `seccompProfile.type: RuntimeDefault`
- `automountServiceAccountToken: false`
- Deny-all `NetworkPolicy` (ingress + egress) attached to every Pod by default

## Repository layout

```text
langchain-kubernetes/
├── python/       # Python package (Poetry)
└── typescript/   # TypeScript package (ESM + CJS dual build)
```

See the per-package READMEs for full configuration references, async usage, file operations, OpenShift compatibility, and development instructions:

- [`python/README.md`](python/README.md)
- [`typescript/README.md`](typescript/README.md)

## Requirements

- A reachable Kubernetes cluster (kind, GKE, EKS, AKS, OpenShift, …)
- NetworkPolicy support in the CNI plugin when `block_network=True` (Calico, Cilium, Weave). The default `kindnet` in kind does **not** support NetworkPolicy — use Calico or set `block_network=False` for local testing.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT
