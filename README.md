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
  - [Multi-turn: persistent sandbox per conversation](#multi-turn-persistent-sandbox-per-conversation)
  - [Operational management](#operational-management)
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

### Multi-turn: persistent sandbox per conversation

`KubernetesSandboxManager.create_agent()` / `createAgent()` returns a ready-to-use
DeepAgents agent. Each turn it reconnects to the same sandbox using the conversation
`thread_id`; if the sandbox has expired it provisions a new one transparently.

#### Python — FastAPI

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from langchain_anthropic import ChatAnthropic
from langgraph.checkpoint.memory import MemorySaver
from langchain_kubernetes import KubernetesSandboxManager, KubernetesProviderConfig

manager = KubernetesSandboxManager(
    KubernetesProviderConfig(
        template_name="python-sandbox-template",
        warm_pool_name="python-pool",
    ),
    ttl_seconds=86400,
    ttl_idle_seconds=1800,
    default_labels={"app": "my-chat-service"},
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

#### Python — `langgraph dev` / LangGraph Platform

```python
# agent.py
from langchain_anthropic import ChatAnthropic
from langchain_kubernetes import KubernetesSandboxManager, KubernetesProviderConfig

manager = KubernetesSandboxManager(
    KubernetesProviderConfig(
        template_name="python-sandbox-template",
        warm_pool_name="python-pool",
    ),
    ttl_idle_seconds=1800,
    default_labels={"app": "my-langgraph-agent"},
)

llm = ChatAnthropic(model="claude-opus-4-6")
graph = manager.create_agent(llm)   # platform provides the checkpointer
```

```json
{
    "dependencies": ["."],
    "graphs": { "agent": "./agent.py:graph" }
}
```

```bash
pip install "langgraph-cli[inmem]"
langgraph dev   # → http://localhost:2024
```

#### TypeScript — Express

```typescript
import express from "express";
import { MemorySaver } from "@langchain/langgraph";
import { ChatAnthropic } from "@langchain/anthropic";
import { KubernetesSandboxManager } from "@bitkaio/langchain-kubernetes";

const manager = new KubernetesSandboxManager(
  { templateName: "python-sandbox-template", routerUrl: "http://sandbox-router-svc:8080", warmPoolName: "python-pool" },
  { ttlSeconds: 86400, ttlIdleSeconds: 1800 },
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

#### TypeScript — `langgraph dev` / LangGraph Platform

```typescript
// agent.ts
import { ChatAnthropic } from "@langchain/anthropic";
import { KubernetesSandboxManager } from "@bitkaio/langchain-kubernetes";

const manager = new KubernetesSandboxManager(
  { templateName: "python-sandbox-template", routerUrl: "http://sandbox-router-svc:8080" },
  { ttlIdleSeconds: 1800 },
);
export const graph = await manager.createAgent(new ChatAnthropic({ model: "claude-opus-4-6" }));
```

```json
{ "graphs": { "agent": "./agent.ts:graph" } }
```

```bash
npx @langchain/langgraph-cli dev   # → http://localhost:2024
```

### Operational management

```python
# List all active sandboxes (optionally filter by label)
response = provider.list(labels={"app": "my-chat-service"})
for sb in response.sandboxes:
    print(sb.id, sb.created_at)

# Delete sandboxes that have been idle > 1 hour
result = provider.cleanup(max_idle_seconds=3600)
print(f"Deleted {result.deleted}, kept {result.kept}")

# Warm pool status
pool = provider.pool_status()
print(f"Available: {pool.available}/{pool.total}")
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
- **Audit trail**: every sandbox carries `langchain-kubernetes.bitkaio.com/managed-by` and `created-at` labels — queryable with `kubectl get pods --show-labels` or your existing observability stack

See [`docs/openshift.md`](docs/openshift.md) for OpenShift-specific notes (SCC, UID ranges, Routes).

## Feature comparison

| Feature                          | langchain-kubernetes | Daytona   | Modal | Runloop |
| -------------------------------- | -------------------- | --------- | ----- | ------- |
| Self-hosted / on-prem            | ✅                   | ❌        | ❌    | ❌      |
| Per-thread sandbox (graph state) | ✅ built-in          | ✅ manual | ❌    | ❌      |
| LangGraph `create_agent_node`    | ✅                   | ❌        | ❌    | ❌      |
| Warm pools                       | ✅                   | ❌        | ❌    | ❌      |
| gVisor / Kata isolation          | ✅                   | ❌        | ❌    | ❌      |
| Network policies per sandbox     | ✅                   | ❌        | ❌    | ❌      |
| TTL auto-cleanup                 | ✅                   | ✅        | ✅    | ❌      |

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
