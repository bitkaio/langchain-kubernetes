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

Full guide: https://agent-sandbox.sigs.k8s.io/docs/getting_started/

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
|------|-------------|----------------|
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
|--|--|--|
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

### Quick Start

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

### Configuration

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

Works the same regardless of mode:

```python
from langchain_anthropic import ChatAnthropic
from deepagents import create_agent
from langchain_kubernetes import KubernetesProvider, KubernetesProviderConfig

# agent-sandbox mode
provider = KubernetesProvider(
    KubernetesProviderConfig(template_name="python-sandbox-template")
)

# or raw mode
provider = KubernetesProvider(
    KubernetesProviderConfig(mode="raw", image="python:3.12-slim")
)

sandbox = provider.get_or_create()
llm = ChatAnthropic(model="claude-opus-4-5")
agent = create_agent(llm, backend=sandbox)

result = agent.invoke({
    "messages": [("user", "Write and run a Python script that prints the Fibonacci sequence")]
})
print(result)

provider.delete(sandbox_id=sandbox.id)
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

## Per-thread sandboxes and lifecycle management

### KubernetesSandboxManager (LangGraph integration)

The easiest way to use `langchain-kubernetes` with LangGraph. Wraps `KubernetesProvider`
and exposes a `backend_factory` callable that LangGraph passes a `RunnableConfig` to:

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

# LangGraph usage
agent = create_deep_agent(model=llm, backend=manager.backend_factory)

# Context manager — cleans up all sandboxes on exit
with KubernetesSandboxManager(config) as manager:
    agent = create_deep_agent(model=llm, backend=manager.backend_factory)
    ...

# Async context manager
async with KubernetesSandboxManager(config) as manager:
    ...

# Manual shutdown
manager.shutdown()       # sync
await manager.ashutdown() # async
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
| `backend_factory` | `Callable` | Sync factory for LangGraph |
| `abackend_factory(config)` | `Coroutine[KubernetesSandbox]` | Async factory |
| `get_sandbox(thread_id)` | `KubernetesSandbox \| None` | Lookup without creating |
| `shutdown()` | `None` | Delete all sandboxes, clear cache |
| `ashutdown()` | `Coroutine` | Async variant |

### Provider — per-thread get_or_create

```python
# Create new sandbox
sandbox = provider.get_or_create()

# Idempotent — returns existing sandbox for this thread, or creates a new one
sandbox = provider.get_or_create(
    thread_id="conv-abc-123",
    labels={"customer": "acme"},        # auto-prefixed with langchain-kubernetes.bitkaio.com/
    ttl_seconds=3600,                   # absolute TTL from creation
    ttl_idle_seconds=600,               # idle TTL from last execute()
)

# Reconnect to a specific sandbox by ID (no K8s API call if in-process cache hit)
sandbox = provider.get_or_create(sandbox_id="existing-id")

# Look up without creating
sandbox = provider.find_by_thread_id("conv-abc-123")  # None if not found

# List all managed sandboxes from the K8s API
response = provider.list()                          # SandboxListResponse
response = provider.list(thread_id="conv-abc-123") # filter by thread
response = provider.list(status="running")          # filter by status
response = provider.list(labels={"customer": "acme"})
for sb in response.sandboxes:
    print(sb.id, sb.thread_id, sb.status, sb.last_activity)
next_page = provider.list(cursor=response.cursor)  # pagination

# Operational methods
result = provider.cleanup()                   # CleanupResult(deleted=[...], kept=N)
result = provider.cleanup(max_idle_seconds=300)  # override per-sandbox idle TTL
stats  = provider.stats()                     # ProviderStats(total, running, warm, idle, thread_ids)
status = provider.pool_status()               # WarmPoolStatus(available, active, total, target)

# Async variants
sandbox  = await provider.aget_or_create(thread_id="conv-abc-123")
response = await provider.alist()
result   = await provider.acleanup()
stats    = await provider.astats()

# Delete (idempotent)
provider.delete(sandbox_id=sandbox.id)
await provider.adelete(sandbox_id=sandbox.id)
```

### New configuration options

The following fields were added to `KubernetesProviderConfig` (both modes unless noted):

| Field | Type | Default | Description |
| ----- | ---- | ------- | ----------- |
| `default_labels` | `dict[str, str] \| None` | `None` | Labels applied to every sandbox (auto-prefixed with `langchain-kubernetes.bitkaio.com/`) |
| `ttl_seconds` | `int \| None` | `None` | Default absolute TTL passed to `get_or_create()` |
| `ttl_idle_seconds` | `int \| None` | `None` | Default idle TTL passed to `get_or_create()` |
| `warm_pool_size` | `int` | `0` | Number of warm Pods to pre-create (raw mode only) |
| `warm_pool_name` | `str \| None` | `None` | `SandboxWarmPool` resource to claim from (agent-sandbox only) |
| `kube_api_url` | `str \| None` | `None` | K8s API URL for SandboxClaim management (agent-sandbox, defaults to in-cluster) |
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
