# langchain-kubernetes

Kubernetes sandbox provider for the [DeepAgents](https://github.com/langchain-ai/deepagents) framework. Available for both Python and TypeScript.

Supports two backend modes:

- **`agent-sandbox` mode** (default, recommended) — integrates with [`kubernetes-sigs/agent-sandbox`](https://github.com/kubernetes-sigs/agent-sandbox). Requires the controller + CRDs installed in the cluster. Provides warm pools, gVisor/Kata isolation, and sub-second startup.
- **`raw` mode** (fallback) — directly manages ephemeral Pods via the Kubernetes API. Works on any cluster with no CRD installation. Uses tar-piped exec for file transfer and a deny-all NetworkPolicy for network isolation.

## Packages

| Package | Registry | Language |
|---|---|---|
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

---

## Use with DeepAgents

```python
from langchain_anthropic import ChatAnthropic
from deepagents import create_agent
from langchain_kubernetes import KubernetesProvider

provider = KubernetesProvider()
sandbox = provider.get_or_create()

llm = ChatAnthropic(model="claude-opus-4-6")
agent = create_agent(llm, backend=sandbox)

result = agent.invoke({"messages": [("user", "Write and run a Python script that prints the Fibonacci sequence")]})
print(result)

provider.delete(sandbox_id=sandbox.id)
```

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

```
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
