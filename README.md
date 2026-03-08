# langchain-kubernetes

Kubernetes sandbox provider for the [DeepAgents](https://github.com/langchain-ai/deepagents) framework. Available for both Python and TypeScript.

Runs each sandbox as an ephemeral Kubernetes Pod with `sleep infinity`. Code execution goes through the Kubernetes exec API (WebSocket). File transfer uses tar-piped exec. Network isolation is enforced with a deny-all `NetworkPolicy` by default.

## Packages

| Package | Registry | Language |
|---|---|---|
| `langchain-kubernetes` | PyPI | Python ≥ 3.10 |
| `langchain-kubernetes` | npm | Node.js ≥ 18 |

## Quick start

### Python

```bash
pip install langchain-kubernetes
```

```python
from langchain_kubernetes import KubernetesProvider

provider = KubernetesProvider()
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
npm install langchain-kubernetes
```

```typescript
import { KubernetesProvider } from "langchain-kubernetes";

const provider = new KubernetesProvider({ image: "python:3.12-slim" });
const sandbox = await provider.getOrCreate();

try {
    const result = await sandbox.execute("python3 -c 'print(42)'");
    console.log(result.output); // "42\n"
} finally {
    await provider.delete(sandbox.id);
}
```

## Use with DeepAgents

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
├── python/       # Python package (hatchling, PEP 621)
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
