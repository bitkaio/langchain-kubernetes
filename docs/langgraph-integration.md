# LangGraph Integration Guide

This guide shows how to integrate `langchain-kubernetes` with LangGraph so that each conversation thread gets its own isolated sandbox.

## Python

### Installation

```bash
pip install langchain-kubernetes[agent-sandbox]   # agent-sandbox mode
pip install langchain-kubernetes[raw]             # raw mode (no CRDs)
```

### Using KubernetesSandboxManager as a LangGraph backend factory

The `KubernetesSandboxManager` wraps `KubernetesProvider` and exposes a `backend_factory` callable that LangGraph's executor can use to resolve a sandbox per thread.

```python
from langchain_kubernetes import KubernetesSandboxManager, KubernetesProviderConfig

# Agent-sandbox mode (recommended)
manager = KubernetesSandboxManager(
    KubernetesProviderConfig(
        mode="agent-sandbox",
        template_name="python-sandbox-template",
        # router is auto-discovered in-cluster; set for local dev:
        # api_url="http://localhost:8001",
    ),
    ttl_seconds=3600,         # absolute TTL: reclaim after 1h
    ttl_idle_seconds=1800,    # idle TTL: reclaim after 30min of no activity
    default_labels={"project": "my-agent", "env": "prod"},
)

# Pass to LangGraph executor
executor = SandboxedExecutor(backend_factory=manager.backend_factory)
```

LangGraph passes a `RunnableConfig` dict (with `configurable.thread_id`) to the factory. The manager extracts the thread ID, looks up an existing sandbox or creates a new one, and caches it for subsequent calls within the same process.

### Async usage

```python
# In an async context, use abackend_factory directly:
sandbox = await manager.abackend_factory({"configurable": {"thread_id": "conv-abc-123"}})
result = await sandbox.aexecute("python3 -c 'print(42)'")
```

### Cleanup

```python
# Context manager (sync):
with KubernetesSandboxManager(config) as manager:
    ...
# all sandboxes are deleted on exit

# Context manager (async):
async with KubernetesSandboxManager(config) as manager:
    ...

# Manual:
manager.shutdown()         # sync
await manager.ashutdown()  # async
```

### Thread-ID lookup and per-conversation persistence

Once a sandbox is created for a `thread_id`, subsequent calls with the same ID return the same sandbox — without hitting the Kubernetes API — via an in-process cache.

If the process restarts, the sandbox is looked up by Kubernetes label selector (`langchain-kubernetes.bitkaio.com/thread-id=<hash>`) on the next call.

```python
# Explicit lookup (no creation)
sandbox = provider.find_by_thread_id("conv-abc-123")
if sandbox:
    sandbox.execute("echo still here")
```

### TTL and auto-cleanup

Sandboxes carry annotations set at creation time:

| Annotation | Meaning |
|---|---|
| `langchain-kubernetes.bitkaio.com/ttl-seconds` | Absolute TTL from `created-at` |
| `langchain-kubernetes.bitkaio.com/ttl-idle-seconds` | Idle TTL from `last-activity` |
| `langchain-kubernetes.bitkaio.com/created-at` | ISO-8601 creation timestamp |
| `langchain-kubernetes.bitkaio.com/last-activity` | Updated after each execute() |

The provider's `cleanup()` method enforces these TTLs:

```python
result = provider.cleanup()
print(f"Deleted: {result.deleted}, Kept: {result.kept}")

# Or with an explicit idle threshold:
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

### Using KubernetesSandboxManager

```typescript
import { KubernetesSandboxManager } from "@bitkaio/langchain-kubernetes";

const manager = new KubernetesSandboxManager(
  {
    mode: "agent-sandbox",
    routerUrl: "http://sandbox-router-svc.default.svc.cluster.local:8080",
    templateName: "python-sandbox-template",
  },
  {
    ttlSeconds: 3600,
    ttlIdleSeconds: 1800,
    defaultLabels: { project: "my-agent", env: "prod" },
  }
);

// Pass to your executor:
const factory = manager.backendFactory;
const sandbox = await factory({ configurable: { thread_id: "conv-abc-123" } });
await sandbox.execute("python3 -c 'print(42)'");
```

### Async cleanup with `await using`

TypeScript 5.2+ supports the `using` statement for async resource management:

```typescript
await using manager = new KubernetesSandboxManager({ ... });
// sandbox cleanup happens automatically when the block exits
```

Or manually:

```typescript
await manager.shutdown();
```

### Low-level provider usage

```typescript
import { KubernetesProvider } from "@bitkaio/langchain-kubernetes";

const provider = new KubernetesProvider({
  mode: "agent-sandbox",
  routerUrl: "http://sandbox-router-svc.default.svc.cluster.local:8080",
  templateName: "python-sandbox-template",
  ttlSeconds: 3600,
  ttlIdleSeconds: 900,
});

// Create / look up by thread:
const sandbox = await provider.getOrCreate({
  threadId: "conv-abc-123",
  labels: { customer: "acme" },
});

// Find existing (no creation):
const existing = await provider.findByThreadId("conv-abc-123");

// List all sandboxes:
const { sandboxes, cursor } = await provider.list({ status: "running" });

// Stats:
const stats = await provider.stats();
console.log(`Total: ${stats.total}, Warm: ${stats.warm}, Idle: ${stats.idle}`);

// Cleanup:
const result = await provider.cleanup(600);  // idle > 10 min
```

---

## Local Development

For local development without a full cluster, use `kubectl port-forward`:

```bash
# agent-sandbox mode
kubectl port-forward svc/sandbox-router-svc 8080:8080

# Then set routerUrl: "http://localhost:8080"
# And kubeApiUrl: "http://localhost:8001" (kubectl proxy)
```

For raw mode, point at any accessible cluster via kubeconfig:

```python
config = KubernetesProviderConfig(
    mode="raw",
    image="python:3.12-slim",
    namespace="default",
)
```

---

## Security Notes

- All sandboxes created by this provider carry the label `langchain-kubernetes.bitkaio.com/managed-by=langchain-kubernetes` for easy RBAC scoping.
- Raw mode enforces network isolation via a deny-all `NetworkPolicy` by default (`blockNetwork=True`/`blockNetwork: true`).
- For regulated environments (OpenShift, PCI-DSS, etc.), see [openshift.md](./openshift.md).
- Use gVisor or Kata Containers runtime classes in agent-sandbox mode for kernel-level isolation.
