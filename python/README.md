# langchain-kubernetes

Kubernetes sandbox provider for [DeepAgents](https://github.com/langchain-ai/deepagents).

Runs every sandbox as an ephemeral Kubernetes Pod with `sleep infinity`. All code execution happens through the Kubernetes exec API (WebSocket). File transfer uses tar-piped exec. Network isolation is enforced with a deny-all `NetworkPolicy` by default.

## Installation

```bash
pip install langchain-kubernetes
```

For async support:

```bash
pip install "langchain-kubernetes[async]"
```

## Quick start

```python
from langchain_kubernetes import KubernetesProvider, KubernetesProviderConfig

provider = KubernetesProvider()

sandbox = provider.get_or_create()
try:
    result = sandbox.execute("python3 -c 'print(2 + 2)'")
    print(result.output)    # "4"
    print(result.exit_code) # 0
finally:
    provider.delete(sandbox_id=sandbox.id)
```

## Configuration

```python
from langchain_kubernetes import KubernetesProvider, KubernetesProviderConfig

config = KubernetesProviderConfig(
    # Namespace where sandbox Pods are created (shared-namespace mode)
    namespace="deepagents-sandboxes",

    # True = each sandbox gets its own namespace (maximum isolation)
    namespace_per_sandbox=False,

    # Container image for the sandbox Pod
    image="python:3.12-slim",

    # Attach a deny-all NetworkPolicy to every sandbox (default: True)
    block_network=True,

    # CPU / memory resource limits
    cpu_request="100m",
    cpu_limit="2",
    memory_request="128Mi",
    memory_limit="512Mi",

    # Seconds to wait for Pod to become Running (raises + cleans up on timeout)
    startup_timeout=120.0,

    # Default per-command timeout in seconds (None = wait indefinitely)
    default_exec_timeout=1800,

    # Path to kubeconfig (None = in-cluster credentials or ~/.kube/config)
    kubeconfig=None,
    context=None,

    # Extra environment variables injected into every sandbox container
    extra_env={"MY_VAR": "value"},

    # UID/GID the sandbox container runs as (set to None for OpenShift)
    run_as_user=1000,
    run_as_group=1000,

    # seccompProfile type (set to None for OpenShift < 4.11)
    seccomp_profile="RuntimeDefault",
)

provider = KubernetesProvider(config=config)
```

## Sandbox lifecycle

### Create / reconnect

```python
# Create a new sandbox
sandbox = provider.get_or_create()
print(sandbox.id)  # e.g. "deepagents-sandbox-a1b2c3d4"

# Reconnect to an existing sandbox by ID
sandbox = provider.get_or_create(sandbox_id="deepagents-sandbox-a1b2c3d4")
```

### List active sandboxes

```python
sandboxes = provider.list()
for s in sandboxes:
    print(s.id)
```

### Delete

```python
provider.delete(sandbox_id=sandbox.id)  # idempotent
```

## Execute commands

```python
result = sandbox.execute("echo hello")
print(result.output)    # "hello\n"
print(result.exit_code) # 0
print(result.truncated) # False

# Per-call timeout override (seconds)
result = sandbox.execute("sleep 60", timeout=5)
print(result.exit_code)  # -1 on timeout
```

## File operations

All file operations from `BaseSandbox` are inherited and work out of the box via `execute()`. File transfer is optimised with tar-based exec.

```python
# Write a file
sandbox.write("/tmp/script.py", "print('hello')\n")

# Read a file
content = sandbox.read("/tmp/script.py")

# Edit a file (string replacement)
sandbox.edit("/tmp/script.py", "hello", "world")

# List a directory
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

## Async usage

```python
import asyncio
from langchain_kubernetes import KubernetesProvider

async def main():
    provider = KubernetesProvider()

    sandbox = await provider.aget_or_create()
    try:
        result = await sandbox.aexecute("echo async")
        print(result.output)
    finally:
        await provider.adelete(sandbox_id=sandbox.id)

asyncio.run(main())
```

Async exec requires the `[async]` extra (`kubernetes_asyncio`).

## Use with DeepAgents

`KubernetesSandbox` implements `SandboxBackendProtocol` and can be passed directly as the backend for a DeepAgent:

```python
from langchain_anthropic import ChatAnthropic
from deepagents import create_agent
from langchain_kubernetes import KubernetesProvider

provider = KubernetesProvider()
sandbox = provider.get_or_create()

llm = ChatAnthropic(model="claude-opus-4-5")
agent = create_agent(llm, backend=sandbox)

result = agent.invoke({"messages": [("user", "Write and run a Python script that prints the Fibonacci sequence")]})
print(result)

provider.delete(sandbox_id=sandbox.id)
```

## Security

Every Pod is created with hardened defaults:

- `runAsNonRoot: true`
- `runAsUser: 1000` / `runAsGroup: 1000` (configurable, see OpenShift below)
- `allowPrivilegeEscalation: false`
- `capabilities.drop: [ALL]`
- `seccompProfile.type: RuntimeDefault` (configurable, see OpenShift below)
- `automountServiceAccountToken: false`

When `block_network=True` (default), a deny-all `NetworkPolicy` is attached to the Pod, blocking all ingress and egress traffic.

## OpenShift

OpenShift's default **restricted SCC** enforces that Pods run with a UID from the namespace's pre-allocated range and does not allow setting an arbitrary `runAsUser`. OpenShift < 4.11 also rejects the `seccompProfile` field.

Set `run_as_user=None`, `run_as_group=None`, and `seccomp_profile=None` to omit those fields and let OpenShift handle them:

```python
config = KubernetesProviderConfig(
    # Let OpenShift assign the UID from the namespace-allocated range
    run_as_user=None,
    run_as_group=None,

    # Omit seccompProfile for OpenShift < 4.11; keep "RuntimeDefault" for >= 4.11
    seccomp_profile=None,

    # Use an image that supports arbitrary UIDs (files writable by group 0)
    image="python:3.12-slim",
)
provider = KubernetesProvider(config=config)
```

The container image must support running as an arbitrary UID. The standard practice is to make application files readable/writable by group `0` (`chmod -R g=u`), as OpenShift follows this convention.

NetworkPolicy enforcement on OpenShift works as-is with both the OVN-Kubernetes and OpenShift SDN CNI plugins.

## Namespace isolation

| Mode | `namespace_per_sandbox` | Sandbox ID format | Cleanup |
|---|---|---|---|
| Shared (default) | `False` | `deepagents-sandbox-<id>` | Pod deleted individually |
| Per-sandbox | `True` | `<namespace>/<pod>` | Entire namespace deleted (cascades) |

## Requirements

- Python ≥ 3.10
- `deepagents >= 0.4.3`
- `kubernetes >= 31.0.0`
- A reachable Kubernetes cluster (local kind cluster, GKE, EKS, AKS, etc.)
- NetworkPolicy support in the CNI plugin (for `block_network=True`). Most plugins support this (Calico, Cilium, Weave). The default `kindnet` in kind does **not** — use Calico or disable `block_network` when testing locally.

## Development

```bash
# Install with dev dependencies
uv venv .venv
uv pip install -e ".[dev]"

# Run unit tests (no cluster required)
.venv/bin/python -m pytest tests/unit/

# Run integration tests (requires a running cluster in your kubeconfig)
.venv/bin/python -m pytest tests/integration/ -m integration
```

## License

MIT
