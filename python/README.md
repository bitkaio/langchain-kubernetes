# langchain-kubernetes

Kubernetes sandbox provider for [DeepAgents](https://github.com/langchain-ai/deepagents), backed by the community-standard [`kubernetes-sigs/agent-sandbox`](https://github.com/kubernetes-sigs/agent-sandbox) controller.

Run AI agent code in isolated, stateful Kubernetes sandboxes — including air-gapped and regulated environments. Supports gVisor, Kata Containers, warm pools for sub-second startup, and all sandbox isolation features from the agent-sandbox controller.

---

## Prerequisites

**This package does not install or manage the agent-sandbox controller.** The following must already be deployed in your cluster before using this package:

1. **agent-sandbox controller + CRDs** — manages `Sandbox`, `SandboxTemplate`, `SandboxClaim`, and `SandboxWarmPool` resources.
2. **sandbox-router** — HTTP gateway that routes traffic from the SDK to sandbox Pods.
3. **A `SandboxTemplate` CR** — defines the sandbox blueprint (image, resources, runtime class, security).

### Install the controller

```bash
export VERSION="v0.1.0"
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/${VERSION}/manifest.yaml
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/${VERSION}/extensions.yaml
```

Full installation guide: https://agent-sandbox.sigs.k8s.io/docs/getting_started/

### Deploy the sandbox-router

The sandbox-router is required for the Python SDK to communicate with sandbox Pods:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/download/${VERSION}/sandbox-router.yaml
```

### Create a SandboxTemplate

Apply the example template from this repo or write your own:

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

---

## Installation

```bash
pip install langchain-kubernetes
```

---

## Quick Start

```python
from langchain_kubernetes import KubernetesProvider, KubernetesProviderConfig

provider = KubernetesProvider(
    KubernetesProviderConfig(template_name="python-sandbox-template")
)

sandbox = provider.get_or_create()
try:
    result = sandbox.execute("python3 -c 'print(2 + 2)'")
    print(result.output)     # "4\n"
    print(result.exit_code)  # 0
finally:
    provider.delete(sandbox_id=sandbox.id)
```

---

## Cluster Setup

### 1. Verify prerequisites

```bash
# Check controller is running
kubectl get pods -n agent-sandbox-system

# Check CRDs are installed
kubectl get crds | grep agents.x-k8s.io

# Check sandbox-router
kubectl get pods -l app=sandbox-router

# Check your SandboxTemplate exists
kubectl get sandboxtemplate python-sandbox-template
```

### 2. Apply a SandboxTemplate

```bash
kubectl apply -f examples/k8s/sandbox-template.yaml
```

### 3. (Optional) Apply a SandboxWarmPool

For sub-second sandbox startup, pre-warm a pool of Pods:

```bash
kubectl apply -f examples/k8s/warm-pool.yaml
```

---

## Usage with DeepAgents

```python
from langchain_anthropic import ChatAnthropic
from deepagents import create_agent
from langchain_kubernetes import KubernetesProvider, KubernetesProviderConfig

provider = KubernetesProvider(
    KubernetesProviderConfig(template_name="python-sandbox-template")
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
deepagents --sandbox kubernetes --template-name python-sandbox-template
```

With gateway mode:

```bash
deepagents --sandbox kubernetes \
  --template-name python-sandbox-template \
  --connection-mode gateway \
  --gateway-name my-gateway
```

---

## Configuration Reference

```python
from langchain_kubernetes import KubernetesProviderConfig

config = KubernetesProviderConfig(
    # Required: SandboxTemplate CR name (must exist in the cluster)
    template_name="python-sandbox-template",

    # Kubernetes namespace where sandboxes are created
    namespace="default",

    # How to connect to the sandbox-router:
    #   "tunnel"  — auto port-forward via kubectl (default, good for local dev)
    #   "gateway" — route through a Kubernetes Gateway resource
    #   "direct"  — connect to an explicit URL (for in-cluster or custom domains)
    connection_mode="tunnel",

    # For gateway mode: name of the Gateway resource
    gateway_name=None,

    # For gateway mode: namespace of the Gateway resource
    gateway_namespace="default",

    # For direct mode: full URL of the sandbox-router
    api_url=None,

    # Port that the sandbox runtime listens on
    server_port=8888,

    # Seconds to wait for a sandbox to become ready before raising TimeoutError
    startup_timeout_seconds=120,

    # Default per-execute() timeout in seconds
    default_exec_timeout=1800,
)
```

### Connection modes

| Mode | When to use | Required config |
|------|-------------|-----------------|
| `tunnel` (default) | Local development, any cluster with `kubectl` available | Nothing extra |
| `gateway` | Production with a Kubernetes Gateway resource | `gateway_name` |
| `direct` | In-cluster agents or custom sandbox-router URL | `api_url` |

---

## Optional: Warm Pools

`SandboxWarmPool` pre-warms a pool of sandbox Pods so they're ready before any agent requests one. This eliminates cold-start latency (container pull + init time).

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

Apply:

```bash
kubectl apply -f examples/k8s/warm-pool.yaml
```

The agent-sandbox controller automatically maintains the pool. When a sandbox is claimed from the pool, a new replacement Pod is started to restore the pool size.

---

## Sandbox Lifecycle

```python
# Create
sandbox = provider.get_or_create()
print(sandbox.id)  # e.g. "python-sandbox-template-a1b2c3d4"

# Reconnect to an existing sandbox (within the same provider instance)
sandbox = provider.get_or_create(sandbox_id="python-sandbox-template-a1b2c3d4")

# List active sandboxes (current provider instance)
sandboxes = provider.list()

# Delete (idempotent)
provider.delete(sandbox_id=sandbox.id)
```

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

### `ImportError: k8s-agent-sandbox package not installed`

```bash
pip install k8s-agent-sandbox
```

### `SandboxTemplate 'my-template' not found in namespace 'default'`

Create the template first:

```bash
kubectl apply -f examples/k8s/sandbox-template.yaml
# or check existing templates:
kubectl get sandboxtemplates
```

### `Cannot reach the sandbox-router`

**Tunnel mode:** Ensure `kubectl` is in your `$PATH` and the sandbox-router Service exists:
```bash
kubectl get svc -l app=sandbox-router
```

**Gateway mode:** Verify the Gateway resource and its external IP:
```bash
kubectl get gateway my-gateway
```

**Direct mode:** Verify the `api_url` is reachable from your client.

### Sandbox startup timeout

Check the agent-sandbox controller logs:
```bash
kubectl logs -n agent-sandbox-system -l app=agent-sandbox-controller
```

Check the Sandbox CR status:
```bash
kubectl get sandboxes -n default
kubectl describe sandbox <sandbox-name>
```

Increase `startup_timeout_seconds` in your config if the cluster is slow.

### Controller or CRDs not installed

Run the installation commands from the Prerequisites section above. Verify:
```bash
kubectl get crds | grep agents.x-k8s.io
kubectl get pods -n agent-sandbox-system
```

---

## Development

```bash
# Install with dev dependencies
uv venv .venv
uv pip install -e ".[dev]"

# Run unit tests (no cluster required)
.venv/bin/python -m pytest tests/unit/

# Run integration tests (requires cluster with agent-sandbox installed)
K8S_INTEGRATION=1 SANDBOX_TEMPLATE=python-sandbox-template \
  .venv/bin/python -m pytest tests/integration/ -m integration
```

---

## License

MIT
