# LangGraph Integration Guide

This guide shows how to integrate `langchain-kubernetes` with LangGraph and DeepAgents so that each conversation thread gets its own isolated, persistent sandbox.

## How per-thread sandbox persistence works

`KubernetesSandboxManager.create_agent()` / `createAgent()` returns a compiled LangGraph+DeepAgents graph that stores the `sandbox_id` as a field in graph state. The LangGraph checkpointer (or LangGraph Platform's built-in one) persists the entire graph state — including `sandbox_id` — between runs for each thread. When the same thread sends its next message, the state is restored and the agent reconnects to the same sandbox automatically.

No Kubernetes label writes are required for this. The `KubernetesSandboxManager` itself is stateless — it holds no in-process sandbox cache.

---

## Python

### Installation

```bash
pip install langchain-kubernetes[agent-sandbox]   # agent-sandbox mode
pip install langchain-kubernetes[raw]             # raw mode (no CRDs)
```

### FastAPI

Pass a `checkpointer` to `create_agent()`. Each HTTP request provides a `thread_id` in the LangGraph config; the checkpointer persists the graph state (including `sandbox_id`) between requests.

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from langchain_anthropic import ChatAnthropic
from langgraph.checkpoint.memory import MemorySaver
from langchain_kubernetes import KubernetesSandboxManager, KubernetesProviderConfig

manager = KubernetesSandboxManager(
    KubernetesProviderConfig(
        template_name="python-sandbox-template",
        warm_pool_name="python-pool",   # optional: sub-second startup
    ),
    ttl_seconds=86400,
    ttl_idle_seconds=1800,
    default_labels={"app": "my-agent"},
)
llm = ChatAnthropic(model="claude-opus-4-6")
agent = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent
    agent = manager.create_agent(llm, checkpointer=MemorySaver())
    yield
    await manager.ashutdown()

app = FastAPI(lifespan=lifespan)

@app.post("/chat/{thread_id}")
async def chat(thread_id: str, message: str):
    result = await agent.ainvoke(
        {"messages": [("user", message)]},
        config={"configurable": {"thread_id": thread_id}},
    )
    return {"reply": result["messages"][-1].content}
```

For production, swap `MemorySaver()` for a persistent checkpointer (e.g., `PostgresSaver`, `RedisSaver`) so state survives process restarts.

### Python — `langgraph dev` / LangGraph Platform

Omit `checkpointer` — the Platform provides its own. Export the compiled graph and point `langgraph.json` at it:

**`agent.py`:**

```python
from langchain_anthropic import ChatAnthropic
from langchain_kubernetes import KubernetesSandboxManager, KubernetesProviderConfig

manager = KubernetesSandboxManager(
    KubernetesProviderConfig(
        template_name="python-sandbox-template",
        warm_pool_name="python-pool",
    ),
    ttl_idle_seconds=1800,
    default_labels={"app": "my-agent"},
)

llm = ChatAnthropic(model="claude-opus-4-6")
graph = manager.create_agent(llm)   # platform provides the checkpointer
```

**`langgraph.json`:**

```json
{
    "dependencies": ["."],
    "graphs": {
        "agent": "./agent.py:graph"
    }
}
```

```bash
pip install "langgraph-cli[inmem]"
langgraph dev   # → http://localhost:2024
```

Each LangGraph thread automatically gets and retains its own sandbox.

### Inside a larger StateGraph

Use `create_agent_node()` to embed the DeepAgents sandbox agent as a node inside your own graph:

```python
from langgraph.graph import StateGraph, END
from typing import Annotated
from typing_extensions import TypedDict
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

class MyState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    sandbox_id: str | None
    # ... your other state fields

sandbox_node = manager.create_agent_node(llm)

builder = StateGraph(MyState)
builder.add_node("sandbox_agent", sandbox_node)
# ... add your other nodes
```

### TTL and auto-cleanup

Sandboxes carry annotations set at creation time:

| Annotation | Meaning |
| --- | --- |
| `langchain-kubernetes.bitkaio.com/ttl-seconds` | Absolute TTL from `created-at` |
| `langchain-kubernetes.bitkaio.com/ttl-idle-seconds` | Idle TTL from `last-activity` |
| `langchain-kubernetes.bitkaio.com/created-at` | ISO-8601 creation timestamp |
| `langchain-kubernetes.bitkaio.com/last-activity` | Updated after each `execute()` |

```python
result = provider.cleanup()
print(f"Deleted: {result.deleted}, Kept: {result.kept}")

# With an explicit idle threshold:
result = provider.cleanup(max_idle_seconds=600)
```

For scheduled reaping, see [reaper-cronjob.yaml](./reaper-cronjob.yaml).

---

## TypeScript

### Installation

```bash
npm install @bitkaio/langchain-kubernetes
npm install @kubernetes/client-node tar-stream   # raw mode only
```

### Express

```typescript
import express from "express";
import { MemorySaver } from "@langchain/langgraph";
import { ChatAnthropic } from "@langchain/anthropic";
import { KubernetesSandboxManager } from "@bitkaio/langchain-kubernetes";

const manager = new KubernetesSandboxManager(
  {
    routerUrl: "http://sandbox-router-svc.default.svc.cluster.local:8080",
    templateName: "python-sandbox-template",
    warmPoolName: "python-pool",
  },
  { ttlSeconds: 86400, ttlIdleSeconds: 1800, defaultLabels: { app: "my-agent" } },
);
const llm = new ChatAnthropic({ model: "claude-opus-4-6" });
const agent = await manager.createAgent(llm, { checkpointer: new MemorySaver() });

const app = express();
app.use(express.json());
app.post("/chat/:threadId", async (req, res) => {
  const result = await agent.invoke(
    { messages: [{ role: "user", content: req.body.message }] },
    { configurable: { thread_id: req.params.threadId } },
  );
  res.json({ reply: result.messages.at(-1)?.content });
});
process.on("SIGTERM", () => manager.shutdown());
app.listen(3000);
```

### TypeScript — `langgraph dev` / LangGraph Platform

**`agent.ts`:**

```typescript
import { ChatAnthropic } from "@langchain/anthropic";
import { KubernetesSandboxManager } from "@bitkaio/langchain-kubernetes";

const manager = new KubernetesSandboxManager(
  {
    routerUrl: "http://sandbox-router-svc.default.svc.cluster.local:8080",
    templateName: "python-sandbox-template",
    warmPoolName: "python-pool",
  },
  { ttlIdleSeconds: 1800, defaultLabels: { app: "my-agent" } },
);

// Omit checkpointer — the platform provides its own
export const graph = await manager.createAgent(new ChatAnthropic({ model: "claude-opus-4-6" }));
```

**`langgraph.json`:**

```json
{ "graphs": { "agent": "./agent.ts:graph" } }
```

```bash
npx @langchain/langgraph-cli dev   # → http://localhost:2024
```

---

## Local Development

For local development without a full cluster, use `kubectl port-forward`:

```bash
# Expose the sandbox-router
kubectl port-forward svc/sandbox-router-svc 8080:8080

# Expose the Kubernetes API (for optional reconnect verification)
kubectl proxy --port=8001
```

Then configure:

```python
config = KubernetesProviderConfig(
    template_name="python-sandbox-template",
    # routerUrl defaults to in-cluster; override for local dev:
    # connection_mode="direct",
    # api_url="http://localhost:8001",
)
```

```typescript
const manager = new KubernetesSandboxManager({
  routerUrl: "http://localhost:8080",
  templateName: "python-sandbox-template",
  kubeApiUrl: "http://localhost:8001",   // optional: enables reconnect verification
});
```

For raw mode, point at any accessible cluster via kubeconfig — no extra port-forwarding needed.

---

## Security Notes

- All sandboxes carry the label `langchain-kubernetes.bitkaio.com/managed-by=langchain-kubernetes` for easy RBAC scoping and `kubectl` queries.
- Raw mode enforces network isolation via a deny-all `NetworkPolicy` by default (`block_network=True` / `blockNetwork: true`).
- For regulated environments (OpenShift, PCI-DSS, etc.), see [openshift.md](./openshift.md).
- Use gVisor or Kata Containers runtime classes in agent-sandbox mode for kernel-level isolation.
