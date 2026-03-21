# langchain-kubernetes

Kubernetes sandbox provider for [DeepAgents](https://github.com/langchain-ai/deepagents). Run AI agent code in isolated, stateful Kubernetes sandboxes.

Two backend modes are supported — pick the one that fits your cluster:

| Mode | When to use | Install |
|------|-------------|---------|
| **`agent-sandbox`** (default) | Full-featured: warm pools, gVisor/Kata, sub-second startup. Requires the [`kubernetes-sigs/agent-sandbox`](https://github.com/kubernetes-sigs/agent-sandbox) controller. | `pip install langchain-kubernetes[agent-sandbox]` |
| **`raw`** | Works on any cluster with no extra controllers or CRDs. Direct Pod management. | `pip install langchain-kubernetes[raw]` |

---

## Installation

```bash
# agent-sandbox mode (recommended when you can install the controller)
pip install langchain-kubernetes[agent-sandbox]

# raw mode (any cluster, no controller required)
pip install langchain-kubernetes[raw]

# both modes
pip install langchain-kubernetes[all]
```

---

## agent-sandbox Mode

### Prerequisites

**This mode does not install or manage the agent-sandbox controller.** The following must already be deployed in your cluster:

1. **agent-sandbox controller + CRDs** — manages `Sandbox`, `SandboxTemplate`, `SandboxClaim`, and `SandboxWarmPool` resources.
2. **sandbox-router** — HTTP gateway that routes traffic from the SDK to sandbox Pods.
3. **A `SandboxTemplate` CR** — defines the sandbox blueprint (image, resources, runtime class, security).

**Install the controller:**

```bash
export VERSION="v0.1.0"
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/${VERSION}/manifest.yaml
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/${VERSION}/extensions.yaml
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/${VERSION}/sandbox-router.yaml
```

Full guide: [agent-sandbox.sigs.k8s.io](https://agent-sandbox.sigs.k8s.io/docs/getting_started/)

**Create a SandboxTemplate:**

```bash
kubectl apply -f examples/k8s/sandbox-template.yaml
```

Example template (see `examples/k8s/sandbox-template.yaml`):

```yaml
apiVersion: extensions.agents.x-k8s.io/v1alpha1
kind: SandboxTemplate
metadata:
  name: python-sandbox-template
  namespace: default
spec:
  podTemplate:
    spec:
      runtimeClassName: gvisor
      containers:
        - name: sandbox
          image: python:3.12-slim
          ports:
            - containerPort: 8888
          resources:
            requests:
              cpu: 250m
              memory: 512Mi
```

### Quick Start

```python
from langchain_kubernetes import KubernetesProvider, KubernetesProviderConfig

provider = KubernetesProvider(
    KubernetesProviderConfig(
        template_name="python-sandbox-template",
    )
)

sandbox = provider.get_or_create()
try:
    result = sandbox.execute("python3 -c 'print(2 + 2)'")
    print(result.output)     # "4\n"
    print(result.exit_code)  # 0
finally:
    provider.delete(sandbox_id=sandbox.id)
```

### Configuration

```python
KubernetesProviderConfig(
    # Required: SandboxTemplate CR name (must exist in the cluster)
    template_name="python-sandbox-template",

    # Kubernetes namespace where sandboxes are created
    namespace="default",

    # How to connect to the sandbox-router:
    #   "tunnel"  — auto port-forward via kubectl (default, good for local dev)
    #   "gateway" — route through a Kubernetes Gateway resource
    #   "direct"  — connect to an explicit URL (for in-cluster or custom domains)
    connection_mode="tunnel",

    # For gateway mode
    gateway_name=None,
    gateway_namespace="default",

    # For direct mode
    api_url=None,

    # Port the sandbox runtime listens on
    server_port=8888,

    # Seconds to wait for sandbox to become ready
    startup_timeout_seconds=120,

    # Default per-execute() timeout in seconds
    default_exec_timeout=1800,
)
```

**Connection modes:**

| Mode | When to use | Required field |
| ---- | ----------- | -------------- |
| `tunnel` (default) | Local dev, `kubectl` available | — |
| `gateway` | Production with a Kubernetes Gateway resource | `gateway_name` |
| `direct` | In-cluster agents or custom sandbox-router URL | `api_url` |

### Optional: Warm Pools

Pre-warm a pool of sandbox Pods to eliminate cold-start latency:

```bash
kubectl apply -f examples/k8s/warm-pool.yaml
```

```yaml
# examples/k8s/warm-pool.yaml
apiVersion: extensions.agents.x-k8s.io/v1alpha1
kind: SandboxWarmPool
metadata:
  name: python-warm-pool
  namespace: default
spec:
  templateRef:
    name: python-sandbox-template
  size: 3  # keep 3 Pods warm at all times
```

---

## Raw Mode

Use this when you cannot install the agent-sandbox controller — locked-down OpenShift clusters, environments where CRD installation requires lengthy approval processes, or air-gapped clusters without access to controller images.

Raw mode directly creates and manages ephemeral Pods via the Kubernetes API. No CRDs, no controllers, no sandbox-router. All work happens through the Kubernetes exec API.

**Tradeoffs vs agent-sandbox mode:**

| | agent-sandbox | raw |
| - | ------------- | --- |
| Controller required | Yes | No |
| CRDs required | Yes | No |
| Warm pools | Yes | No |
| gVisor / Kata | Yes (via SandboxTemplate) | Depends on cluster |
| Startup time | Sub-second (warm) | ~5–30s (Pod scheduling) |
| Pod-level config | In SandboxTemplate CRD | In `KubernetesProviderConfig` |

### RBAC

The process running this package needs a ServiceAccount / kubeconfig with:

```yaml
rules:
- apiGroups: [""]
  resources: ["pods", "pods/exec", "pods/log", "namespaces"]
  verbs: ["get", "list", "create", "delete", "watch"]
- apiGroups: ["networking.k8s.io"]
  resources: ["networkpolicies"]
  verbs: ["get", "create", "delete"]
- apiGroups: [""]
  resources: ["resourcequotas"]
  verbs: ["get", "create", "delete"]
```

The sandbox Pod's own ServiceAccount has no RBAC bindings (`automountServiceAccountToken: false`).

### Raw mode quick start

```python
from langchain_kubernetes import KubernetesProvider, KubernetesProviderConfig

provider = KubernetesProvider(
    KubernetesProviderConfig(
        mode="raw",
        namespace="default",
        image="python:3.12-slim",
    )
)

sandbox = provider.get_or_create()
try:
    result = sandbox.execute("python3 -c 'print(2 + 2)'")
    print(result.output)     # "4\n"
    print(result.exit_code)  # 0
finally:
    provider.delete(sandbox_id=sandbox.id)
```

### Raw mode configuration

```python
KubernetesProviderConfig(
    mode="raw",

    # Kubernetes namespace where Pods are created
    namespace="default",

    # Container image
    image="python:3.12-slim",
    image_pull_policy="IfNotPresent",
    image_pull_secrets=[],          # list of Secret names

    # Working directory inside the container
    workdir="/workspace",

    # Pod entrypoint (default: sleep infinity — all work via exec)
    command=["sleep", "infinity"],

    # Environment variables
    env={"MY_VAR": "value"},

    # Resource requests and limits
    cpu_request="100m",
    cpu_limit="1000m",
    memory_request="256Mi",
    memory_limit="1Gi",
    ephemeral_storage_limit="5Gi",

    # Network isolation: deny-all NetworkPolicy (strongly recommended)
    block_network=True,

    # Security context
    run_as_user=1000,
    run_as_group=1000,
    seccomp_profile="RuntimeDefault",  # or "Localhost"

    # Per-sandbox namespace (stronger isolation, slower, more RBAC)
    namespace_per_sandbox=False,

    # ServiceAccount for the sandbox Pod (default: none)
    service_account=None,

    # Scheduling
    node_selector={},
    tolerations=[],

    # Extra volumes / mounts
    volumes=[],
    volume_mounts=[],
    init_containers=[],

    # Low-level Pod spec overrides (deep-merged into spec)
    pod_template_overrides=None,

    # Pod annotations
    extra_annotations={},

    # Shell script run as first exec after creation
    setup_script=None,

    # Timeouts
    startup_timeout_seconds=120,
    default_exec_timeout=1800,
)
```

### Security defaults

Raw mode Pods always enforce:

```yaml
automountServiceAccountToken: false
securityContext:
  runAsNonRoot: true
  runAsUser: 1000       # configurable
  runAsGroup: 1000      # configurable
containers:
  - securityContext:
      allowPrivilegeEscalation: false
      capabilities:
        drop: ["ALL"]
      seccompProfile:
        type: RuntimeDefault   # configurable
```

---

## Usage with DeepAgents

`KubernetesSandbox` plugs directly into
[DeepAgents](https://github.com/langchain-ai/deepagents) via the `backend` parameter of
`create_deep_agent`. When a sandbox backend is set, the agent automatically gains an
`execute` tool for running shell commands in addition to the standard filesystem tools
(`ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`).

### One-shot usage

Create one sandbox, run one task, delete it:

```python
from langchain_anthropic import ChatAnthropic
from deepagents import create_deep_agent
from langchain_kubernetes import KubernetesProvider, KubernetesProviderConfig

provider = KubernetesProvider(KubernetesProviderConfig(
    template_name="python-sandbox-template",
    # mode="raw", image="python:3.12-slim",   # raw mode alternative
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

For multi-turn applications you need the same sandbox to survive across turns — installed
packages, written files, and shell state must all be retained between messages.

`KubernetesSandboxManager.create_agent()` returns a ready-to-use DeepAgents agent that
handles this automatically. Each turn it reconnects to the same sandbox using the
conversation `thread_id`; if the sandbox has expired it provisions a new one
transparently. No Kubernetes label writes and no direct cluster API access required.

#### Behind FastAPI

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from langchain_anthropic import ChatAnthropic
from langgraph.checkpoint.memory import MemorySaver
from langchain_kubernetes import KubernetesSandboxManager, KubernetesProviderConfig

llm = ChatAnthropic(model="claude-opus-4-6")
manager = KubernetesSandboxManager(
    KubernetesProviderConfig(
        template_name="python-sandbox-template",
        connection_mode="gateway",
        gateway_name="my-gateway",
    ),
    ttl_idle_seconds=1800,   # sandbox expires after 30 min of inactivity
)

agent = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global agent
    # MemorySaver keeps conversation state in-process.
    # Replace with PostgresSaver / RedisSaver for multi-process deployments.
    agent = manager.create_agent(llm, checkpointer=MemorySaver())
    yield
    await manager.acleanup()

app = FastAPI(lifespan=lifespan)

@app.post("/chat/{thread_id}")
async def chat(thread_id: str, message: str):
    result = await agent.ainvoke(
        {"messages": [("user", message)]},
        config={"configurable": {"thread_id": thread_id}},
    )
    return {"reply": result["messages"][-1].content}
```

Each `thread_id` gets its own sandbox. The first request for a thread provisions a
new sandbox; every subsequent request reconnects to the same one.

#### With `langgraph dev` / LangGraph Platform

Yes — **each LangGraph thread automatically gets its own persistent sandbox.**
`create_agent()` stores the `sandbox_id` as a field in graph state. The LangGraph
Platform (and `langgraph dev`) checkpoint the entire graph state — including
`sandbox_id` — between runs for each thread. When the same thread sends its next
message, the platform restores the state and the agent reconnects to the same sandbox
automatically. No extra configuration is needed.

Export the compiled graph from `agent.py` and point `langgraph.json` at it — that's all:

**`agent.py`:**

```python
from langchain_anthropic import ChatAnthropic
from langchain_kubernetes import KubernetesSandboxManager, KubernetesProviderConfig

llm = ChatAnthropic(model="claude-opus-4-6")

manager = KubernetesSandboxManager(
    KubernetesProviderConfig(
        template_name="python-sandbox-template",
        connection_mode="gateway",
        gateway_name="my-gateway",
        warm_pool_name="python-pool",   # optional: sub-second startup
    ),
    ttl_idle_seconds=1800,
    default_labels={"app": "my-agent"},
)

# The platform provides the checkpointer — pass None here
graph = manager.create_agent(llm)
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
langgraph dev
# → http://localhost:2024
```

Interact via the LangGraph SDK — threads and sandbox persistence are handled automatically:

```python
from langgraph_sdk import get_client

client = get_client(url="http://localhost:2024")
thread = await client.threads.create()

# First run — provisions a sandbox for this thread
await client.runs.create(
    thread_id=thread["thread_id"],
    assistant_id="agent",
    input={"messages": [{"role": "user", "content": "Install pandas and analyse the iris dataset"}]},
)

# Second run — reconnects to the same sandbox automatically
await client.runs.create(
    thread_id=thread["thread_id"],
    assistant_id="agent",
    input={"messages": [{"role": "user", "content": "Now plot a histogram of petal length"}]},
)
```

#### Custom `system_prompt` and tools

Extra keyword arguments to `create_agent` are forwarded to `create_deep_agent`:

```python
graph = manager.create_agent(
    llm,
    system_prompt="You are a data analyst. Always save outputs to /workspace/output/.",
    # tools=[my_custom_tool],
)
```

---

## Usage with CLI

```bash
# agent-sandbox mode
deepagents --sandbox kubernetes --template-name python-sandbox-template

# gateway mode
deepagents --sandbox kubernetes \
  --template-name python-sandbox-template \
  --connection-mode gateway \
  --gateway-name my-gateway

# raw mode
deepagents --sandbox kubernetes --mode raw
```

---

## Sandbox lifecycle management

### KubernetesSandboxManager (LangGraph integration)

`KubernetesSandboxManager` wraps `KubernetesProvider` and provides `create_agent_node()` —
the primary integration point for LangGraph applications. See the
[Usage with DeepAgents](#usage-with-deepagents) section above for full examples.

```python
from langchain_kubernetes import KubernetesSandboxManager, KubernetesProviderConfig

manager = KubernetesSandboxManager(
    KubernetesProviderConfig(
        template_name="python-sandbox-template",
    ),
    ttl_seconds=3600,         # reclaim after 1h regardless of activity
    ttl_idle_seconds=1800,    # reclaim after 30 min of inactivity
    default_labels={"project": "my-agent", "env": "prod"},
)

# Primary: LangGraph node factory — see "Usage with DeepAgents" section
node_fn = manager.create_agent_node(llm)

# Lower-level: acquire a sandbox from state manually
sandbox = await manager.get_or_reconnect(sandbox_id)   # async

# Operational
await manager.acleanup()     # delete expired sandboxes
await manager.ashutdown()    # delete all sandboxes

# Context manager — calls shutdown() on exit
async with KubernetesSandboxManager(config) as manager:
    ...
```

**`KubernetesSandboxManager` constructor:**

| Parameter | Type | Default | Description |
| --------- | ---- | ------- | ----------- |
| `provider_config` | `KubernetesProviderConfig` | required | Provider configuration |
| `ttl_seconds` | `int \| None` | `None` | Absolute TTL from creation |
| `ttl_idle_seconds` | `int \| None` | `None` | Idle TTL from last `execute()` |
| `default_labels` | `dict \| None` | `None` | Labels applied to every sandbox (auto-prefixed) |

**Methods:**

| Method | Returns | Description |
| ------ | ------- | ----------- |
| `create_agent(model, *, checkpointer=None, **kwargs)` | `CompiledGraph` | Returns a compiled DeepAgents agent with sandbox persistence (primary integration point) |
| `create_agent_node(model, *, state_sandbox_key="sandbox_id", **kwargs)` | `Callable` | Returns a single LangGraph node; use when building a multi-node graph |
| `get_or_reconnect(sandbox_id)` | `Coroutine[KubernetesSandbox]` | Reconnect or create; for custom node logic |
| `cleanup(max_idle_seconds?)` | `CleanupResult` | Delete expired sandboxes |
| `acleanup(max_idle_seconds?)` | `Coroutine[CleanupResult]` | Async variant |
| `shutdown()` | `None` | Delete all sandboxes |
| `ashutdown()` | `Coroutine` | Async variant |

### Provider — get_or_create and reconnect

The provider is stateless: it does not cache sandboxes in memory. Callers are responsible for
persisting `sandbox.id` between calls (LangGraph handles this via its checkpointer).

```python
# Create new sandbox
sandbox = provider.get_or_create()

# Reconnect to existing sandbox, or create a new one if it no longer exists
sandbox = provider.get_or_create(
    sandbox_id="existing-id",           # from LangGraph state
    labels={"customer": "acme"},        # auto-prefixed with langchain-kubernetes.bitkaio.com/
    ttl_seconds=3600,                   # absolute TTL from creation
    ttl_idle_seconds=600,               # idle TTL from last execute()
)

# List all managed sandboxes from the K8s API
response = provider.list()
response = provider.list(status="running")
response = provider.list(labels={"customer": "acme"})
for sb in response.sandboxes:
    print(sb.id, sb.status, sb.created_at)
next_page = provider.list(cursor=response.cursor)  # pagination

# Operational methods
result = provider.cleanup()                    # CleanupResult(deleted=[...], kept=N)
result = provider.cleanup(max_idle_seconds=300)
status = provider.pool_status()                # WarmPoolStatus(available, active, total, target)

# Async variants
sandbox  = await provider.aget_or_create(sandbox_id="existing-id")
response = await provider.alist()
result   = await provider.acleanup()

# Delete (idempotent)
provider.delete(sandbox_id=sandbox.id)
await provider.adelete(sandbox_id=sandbox.id)
```

### Configuration options

| Field | Type | Default | Description |
| ----- | ---- | ------- | ----------- |
| `default_labels` | `dict[str, str] \| None` | `None` | Labels applied to every sandbox (auto-prefixed with `langchain-kubernetes.bitkaio.com/`) |
| `ttl_seconds` | `int \| None` | `None` | Default absolute TTL passed to `get_or_create()` |
| `ttl_idle_seconds` | `int \| None` | `None` | Default idle TTL passed to `get_or_create()` |
| `warm_pool_size` | `int` | `0` | Number of warm Pods to pre-create (raw mode only) |
| `warm_pool_name` | `str \| None` | `None` | `SandboxWarmPool` resource to claim from (agent-sandbox only) |
| `kube_api_url` | `str \| None` | `None` | K8s API URL for optional sandbox existence verification (agent-sandbox). Auto-detected when running in-cluster. |
| `kube_token` | `str \| None` | `None` | Bearer token for K8s API (agent-sandbox; auto-reads in-cluster token if unset) |

### Warm pool configuration

**agent-sandbox mode** — use a `SandboxWarmPool` CRD (managed by the controller):

```yaml
apiVersion: extensions.agents.x-k8s.io/v1alpha1
kind: SandboxWarmPool
metadata:
  name: python-pool
  namespace: default
spec:
  templateName: python-sandbox-template
  size: 5
```

```python
provider = KubernetesProvider(KubernetesProviderConfig(
    template_name="python-sandbox-template",
    warm_pool_name="python-pool",
))
```

**raw mode** — built-in provider-managed pool:

```python
provider = KubernetesProvider(KubernetesProviderConfig(
    mode="raw",
    warm_pool_size=3,
))
```

See [`docs/warm-pool.yaml`](../docs/warm-pool.yaml) for full examples.

## Execute Commands

```python
result = sandbox.execute("echo hello")
print(result.output)     # "hello\n"
print(result.exit_code)  # 0
print(result.truncated)  # False

# Per-call timeout
result = sandbox.execute("sleep 60", timeout=5)
```

## File Operations

All `BaseSandbox` filesystem helpers work via `execute()` and are inherited automatically:

```python
# Write / read files
sandbox.write("/tmp/script.py", "print('hello')\n")
content = sandbox.read("/tmp/script.py")

# Edit (string replacement)
sandbox.edit("/tmp/script.py", "hello", "world")

# List directory
entries = sandbox.ls_info("/tmp")

# Glob
matches = sandbox.glob_info("**/*.py", path="/app")

# Grep
hits = sandbox.grep_raw("def main", path="/app")

# Batch upload (bytes)
sandbox.upload_files([
    ("/app/data.csv", b"col1,col2\n1,2\n"),
    ("/app/config.json", b'{"key": "val"}'),
])

# Batch download (bytes)
responses = sandbox.download_files(["/app/output.txt"])
print(responses[0].content)
```

## Async Usage

```python
import asyncio
from langchain_kubernetes import KubernetesProvider, KubernetesProviderConfig

async def main():
    provider = KubernetesProvider(
        KubernetesProviderConfig(template_name="python-sandbox-template")
    )
    sandbox = await provider.aget_or_create()
    try:
        result = await sandbox.aexecute("echo async")
        print(result.output)
    finally:
        await provider.adelete(sandbox_id=sandbox.id)

asyncio.run(main())
```

---

## Troubleshooting

### Stale `sandbox_id` after sandbox expiry

**Symptom:** An `execute()` call fails with a connection error or `SandboxNotFoundError`
on the second (or later) turn of a conversation.

**Why this happens:** The sandbox TTL elapsed between turns. The `sandbox_id` stored in
LangGraph graph state points to a sandbox that no longer exists.

**How it is handled automatically:** `get_or_create(sandbox_id=...)` catches
`SandboxNotFoundError` and transparently provisions a new sandbox. The new `sandbox.id`
differs from the `sandbox_id` read from state, so the node writes the new ID back to state:

```python
if sandbox.id != sandbox_id:
    updates[state_sandbox_key] = sandbox.id   # LangGraph checkpointer persists this
```

The agent starts fresh in the new sandbox. Any files or installed packages from the
previous session are gone — use longer TTLs or a setup script if persistence matters.

**To increase sandbox lifetime:**

```python
manager = KubernetesSandboxManager(
    KubernetesProviderConfig(template_name="python-sandbox-template"),
    ttl_seconds=86400,       # keep sandbox for up to 24h
    ttl_idle_seconds=3600,   # reclaim after 1h of inactivity
)
```

### `ImportError: ... requires the 'k8s-agent-sandbox' package`

```bash
pip install langchain-kubernetes[agent-sandbox]
```

### `ImportError: ... requires the 'kubernetes' package`

```bash
pip install langchain-kubernetes[raw]
```

### `SandboxTemplate 'my-template' not found in namespace 'default'`

Create the template first:

```bash
kubectl apply -f examples/k8s/sandbox-template.yaml
# or list existing templates:
kubectl get sandboxtemplates
```

### `Cannot reach the sandbox-router` (agent-sandbox mode)

**Tunnel mode:** Ensure `kubectl` is in `$PATH` and the sandbox-router Service exists:

```bash
kubectl get svc -l app=sandbox-router
```

**Gateway mode:** Verify the Gateway resource:

```bash
kubectl get gateway my-gateway
```

**Direct mode:** Verify `api_url` is reachable from your client.

### Sandbox startup timeout (agent-sandbox mode)

```bash
kubectl logs -n agent-sandbox-system -l app=agent-sandbox-controller
kubectl get sandboxes -n default
kubectl describe sandbox <sandbox-name>
```

Increase `startup_timeout_seconds` if the cluster is slow.

### Pod not reaching Running phase (raw mode)

```bash
kubectl get pods -n default -l app.kubernetes.io/managed-by=deepagents
kubectl describe pod deepagents-<sandbox-id> -n default
kubectl get events -n default --sort-by='.lastTimestamp'
```

Common causes: image pull failures, insufficient resources, PodSecurityPolicy/OPA admission rejections.

### Controller or CRDs not installed (agent-sandbox mode)

```bash
kubectl get crds | grep agents.x-k8s.io
kubectl get pods -n agent-sandbox-system
```

Run the installation commands from the Prerequisites section above, or switch to `mode="raw"`.

---

## Development

```bash
# Install with dev dependencies (both modes)
uv venv .venv
uv pip install -e ".[all,dev]"

# Run unit tests (no cluster required)
.venv/bin/python -m pytest tests/unit/

# Run agent-sandbox integration tests (requires cluster with controller)
K8S_INTEGRATION=1 SANDBOX_TEMPLATE=python-sandbox-template \
  .venv/bin/python -m pytest tests/integration/ -m agent_sandbox

# Run raw mode integration tests (requires any plain Kubernetes cluster)
.venv/bin/python -m pytest tests/integration/ -m raw_k8s
```

---

## License

MIT
