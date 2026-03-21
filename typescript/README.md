# @bitkaio/langchain-kubernetes

Kubernetes sandbox provider for the [DeepAgents](https://github.com/langchain-ai/deepagents) framework (TypeScript).

Supports two backend modes:

| Mode | Requirements | Best for |
|------|-------------|----------|
| **`agent-sandbox`** (default) | [`kubernetes-sigs/agent-sandbox`](https://github.com/kubernetes-sigs/agent-sandbox) controller + CRDs | Production — warm pools, gVisor/Kata, sub-second startup |
| **`raw`** | Any cluster, `@kubernetes/client-node` + `tar-stream` | Dev / clusters where you can't install CRDs |

## Installation

```bash
# agent-sandbox mode — no extra deps (fetch is built in)
npm install @bitkaio/langchain-kubernetes

# raw mode — additional deps required
npm install @bitkaio/langchain-kubernetes @kubernetes/client-node tar-stream
```

## Quick start

### agent-sandbox mode

```typescript
import { KubernetesProvider } from "@bitkaio/langchain-kubernetes";

const provider = new KubernetesProvider({
  mode: "agent-sandbox",
  routerUrl: "http://sandbox-router-svc.default.svc.cluster.local:8080",
  templateName: "python-sandbox-template",
});

const sandbox = await provider.getOrCreate();

const result = await sandbox.execute("python3 -c 'print(2 + 2)'");
console.log(result.output);   // "4\n"
console.log(result.exitCode); // 0

await provider.delete(sandbox.id);
```

### raw mode

```typescript
import { KubernetesProvider } from "@bitkaio/langchain-kubernetes";

const provider = new KubernetesProvider({
  mode: "raw",
  image: "python:3.12-slim",
});

const sandbox = await provider.getOrCreate();
const result  = await sandbox.execute("python3 -c 'print(42)'");
await provider.delete(sandbox.id);
```

## Configuration reference

### Shared options

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mode` | `"agent-sandbox" \| "raw"` | `"agent-sandbox"` | Backend mode |
| `namespace` | `string` | `"deepagents-sandboxes"` | Kubernetes namespace |
| `startupTimeoutMs` | `number` | `120_000` | Ms to wait for sandbox to be ready |
| `executeTimeoutMs` | `number` | `300_000` | Default ms per `execute()` call |

### agent-sandbox mode options

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `routerUrl` | `string` | Yes | URL of the sandbox-router service |
| `templateName` | `string` | Yes | `SandboxTemplate` name to instantiate |
| `serverPort` | `number` | No (8888) | Port the sandbox runtime listens on |
| `kubeApiUrl` | `string` | No | Kubernetes API URL. Defaults to in-cluster URL. Use `http://localhost:8001` for `kubectl proxy`. |
| `kubeToken` | `string` | No | Bearer token for k8s API. Auto-read from service account if omitted. |

### raw mode options

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `image` | `string` | `"python:3.12-slim"` | Container image |
| `imagePullPolicy` | `string` | `"IfNotPresent"` | Image pull policy |
| `workdir` | `string` | `"/workspace"` | Working directory inside container |
| `command` | `string[]` | `["sleep", "infinity"]` | Container entrypoint |
| `env` | `Record<string, string>` | — | Extra environment variables |
| `cpuRequest` / `cpuLimit` | `string` | `"100m"` / `"1000m"` | CPU resources |
| `memoryRequest` / `memoryLimit` | `string` | `"256Mi"` / `"1Gi"` | Memory resources |
| `ephemeralStorageLimit` | `string` | `"5Gi"` | Ephemeral storage limit |
| `blockNetwork` | `boolean` | `true` | Attach deny-all NetworkPolicy |
| `runAsUser` / `runAsGroup` | `number` | `1000` / `1000` | UID/GID inside container |
| `seccompProfile` | `string` | `"RuntimeDefault"` | seccomp profile type |
| `namespacePerSandbox` | `boolean` | `false` | Give each sandbox its own namespace |
| `kubeconfigPath` | `string` | — | Path to kubeconfig file |
| `context` | `string` | — | Kubeconfig context to use |

## Sandbox API

Every sandbox extends `BaseSandbox` from the `deepagents` package:

```typescript
// Execute a shell command
const result = await sandbox.execute("ls -la /workspace");
// result.output    — combined stdout + stderr
// result.exitCode  — process exit code
// result.truncated — true if output was capped at outputLimitBytes

// Upload files
const results = await sandbox.uploadFiles([
  ["/workspace/script.py", Buffer.from("print('hello')")],
  ["/workspace/data.json", Buffer.from('{"key": "value"}')],
]);
// results[0].error — null on success, "file_not_found" | "permission_denied" etc. on failure

// Download files
const downloads = await sandbox.downloadFiles(["/workspace/output.txt"]);
// downloads[0].content — Uint8Array | null
// downloads[0].error   — null on success

// BaseSandbox also provides higher-level helpers built on execute():
// sandbox.ls(), sandbox.read(), sandbox.write(), sandbox.glob(), ...
```

## Usage with DeepAgents

`KubernetesSandbox` implements `SandboxBackendProtocol`, so it plugs directly into
[DeepAgents](https://github.com/langchain-ai/deepagents) via the `backend` parameter of
`createDeepAgent`. When a sandbox backend is set, the agent automatically gains an
`execute` tool for running shell commands in addition to the standard filesystem tools.

### One-shot usage

```typescript
import { ChatAnthropic } from "@langchain/anthropic";
import { createDeepAgent } from "deepagents";
import { KubernetesProvider } from "@bitkaio/langchain-kubernetes";

// Works with either mode — swap the config, nothing else changes
const provider = new KubernetesProvider({
  routerUrl: "http://sandbox-router-svc.default.svc.cluster.local:8080",
  templateName: "python-sandbox-template",   // agent-sandbox mode
  // mode: "raw", image: "python:3.12-slim", // raw mode alternative
});

const sandbox = await provider.getOrCreate();
const llm = new ChatAnthropic({ model: "claude-opus-4-6" });
const agent = createDeepAgent({ model: llm, backend: sandbox });

const result = await agent.invoke({
  messages: [{ role: "user", content: "Write and run a Python script that prints the Fibonacci sequence" }],
});
console.log(result.messages.at(-1)?.content);

await provider.delete(sandbox.id);
```

### Per-thread sandbox management (multi-user / multi-turn)

`getOrCreate({ threadId })` is idempotent: it returns the existing sandbox for a thread
if one is running, and creates one only on the first call. Installed packages, written
files, and shell state persist between turns on the same thread.

```typescript
import { ChatAnthropic } from "@langchain/anthropic";
import { createDeepAgent } from "deepagents";
import { KubernetesProvider } from "@bitkaio/langchain-kubernetes";

const provider = new KubernetesProvider({
  routerUrl: "http://sandbox-router-svc.default.svc.cluster.local:8080",
  templateName: "python-sandbox-template",
  ttlIdleSeconds: 1800,   // auto-delete sandbox after 30 min of inactivity
});
const llm = new ChatAnthropic({ model: "claude-opus-4-6" });

async function handleMessage(threadId: string, userMessage: string): Promise<string> {
  // Idempotent: reconnects to existing sandbox, creates only on first call
  const sandbox = await provider.getOrCreate({ threadId, ttlSeconds: 86400 });
  const agent = createDeepAgent({ model: llm, backend: sandbox });

  const result = await agent.invoke(
    { messages: [{ role: "user", content: userMessage }] },
    { configurable: { thread_id: threadId } },  // LangGraph checkpoint key
  );
  return result.messages.at(-1)?.content ?? "";
}

// Both calls run in the same sandbox — state persists between turns
await handleMessage("user-42-session-7", "Install numpy and create a random 3×3 matrix");
await handleMessage("user-42-session-7", "Print the matrix you just created");
```

### `KubernetesSandboxManager` — fully automatic thread wiring

`KubernetesSandboxManager` owns the entire sandbox lifecycle and exposes a
`backendFactory` callable that `createDeepAgent` accepts as its `backend` argument.
The factory extracts `threadId` from the LangGraph `RunnableConfig` automatically:

```typescript
import { ChatAnthropic } from "@langchain/anthropic";
import { createDeepAgent } from "deepagents";
import { KubernetesSandboxManager } from "@bitkaio/langchain-kubernetes";

const manager = new KubernetesSandboxManager(
  {
    routerUrl: "http://sandbox-router-svc.default.svc.cluster.local:8080",
    templateName: "python-sandbox-template",
    warmPoolName: "python-pool",     // claim from a pre-warmed pool
  },
  {
    ttlSeconds: 86400,       // absolute TTL from creation (24 h)
    ttlIdleSeconds: 1800,    // idle TTL from last execute() (30 min)
    defaultLabels: { app: "my-chat-service" },
  },
);

const llm = new ChatAnthropic({ model: "claude-opus-4-6" });

// backendFactory is (config: RunnableConfig) => Promise<KubernetesSandbox>
const agent = createDeepAgent({ model: llm, backend: manager.backendFactory });

const result = await agent.invoke(
  { messages: [{ role: "user", content: "Set up a data pipeline for the iris dataset" }] },
  { configurable: { thread_id: "session-abc-123" } },
);

// Second turn — same thread_id → same sandbox, files from turn 1 still there
const result2 = await agent.invoke(
  { messages: [{ role: "user", content: "Plot a histogram of petal length" }] },
  { configurable: { thread_id: "session-abc-123" } },
);

await manager.asyncShutdown();
```

#### Express / Hono usage

```typescript
import express from "express";
import { ChatAnthropic } from "@langchain/anthropic";
import { createDeepAgent } from "deepagents";
import { KubernetesSandboxManager } from "@bitkaio/langchain-kubernetes";

const manager = new KubernetesSandboxManager(
  { routerUrl: "http://sandbox-router-svc:8080", templateName: "python-sandbox-template" },
  { ttlIdleSeconds: 1800 },
);
const llm = new ChatAnthropic({ model: "claude-opus-4-6" });
const agent = createDeepAgent({ model: llm, backend: manager.backendFactory });

const app = express();
app.use(express.json());

app.post("/chat/:threadId", async (req, res) => {
  const result = await agent.invoke(
    { messages: [{ role: "user", content: req.body.message }] },
    { configurable: { thread_id: req.params.threadId } },
  );
  res.json({ reply: result.messages.at(-1)?.content });
});

process.on("SIGTERM", () => manager.asyncShutdown());
app.listen(3000);
```

### Hosting with `langgraph dev` / LangGraph Platform

When you run DeepAgents via the LangGraph CLI (`langgraph dev`) or deploy to LangGraph
Platform, the **server** calls the graph for you — you never write `.invoke()` yourself.
The server creates threads (each with a unique `thread_id`), injects it into
`config.configurable.thread_id` for every run, and calls your graph.

`manager.backendFactory` is already designed for this: it receives the `RunnableConfig`
that the server has already populated, extracts `thread_id`, and routes to the right
sandbox.

**`agent.ts`** — module-level singletons, export the compiled graph:

```typescript
import { ChatAnthropic } from "@langchain/anthropic";
import { createDeepAgent } from "deepagents";
import { KubernetesSandboxManager } from "@bitkaio/langchain-kubernetes";

// Created once when the server imports this module — not per-request
export const manager = new KubernetesSandboxManager(
  {
    routerUrl: "http://sandbox-router-svc.default.svc.cluster.local:8080",
    templateName: "python-sandbox-template",
    warmPoolName: "python-pool",
  },
  { ttlIdleSeconds: 1800, defaultLabels: { app: "my-langgraph-agent" } },
);

const llm = new ChatAnthropic({ model: "claude-opus-4-6" });

// The server points langgraph.json at this export
export const graph = createDeepAgent({ model: llm, backend: manager.backendFactory });
```

**`langgraph.json`:**

```json
{
    "graphs": {
        "agent": "./agent.ts:graph"
    }
}
```

```bash
npx @langchain/langgraph-cli dev
# → http://localhost:2024
```

Interact via the LangGraph SDK — the server injects `thread_id` automatically:

```typescript
import { Client } from "@langchain/langgraph-sdk";

const client = new Client({ apiUrl: "http://localhost:2024" });

// Create a thread — maps 1:1 to a KubernetesSandbox on the first run
const thread = await client.threads.create();

// First run: manager creates sandbox, labels it with thread_id
await client.runs.create(thread.thread_id, "agent", {
  input: { messages: [{ role: "user", content: "Install pandas and analyse the iris dataset" }] },
});

// Second run: manager finds existing sandbox by label selector — same filesystem state
await client.runs.create(thread.thread_id, "agent", {
  input: { messages: [{ role: "user", content: "Now plot a histogram of petal length" }] },
});
```

### How thread_id flows through the stack

```text
── Direct .invoke() ──────────────────────────────────────────────────────────
agent.invoke(input, { configurable: { thread_id: "abc" } })
  │
  └─▶ DeepAgents calls backendFactory(toolRuntime)            ─┐
                                                                │
── langgraph dev / LangGraph Platform ──────────────────────── │ same path
POST /runs  { thread_id: "abc", input: {...} }                  │
  │                                                             │
  └─▶ Server injects thread_id into RunnableConfig             │
        └─▶ server calls graph(input, config)                   │
              └─▶ DeepAgents calls backendFactory(toolRuntime) ─┘
                    │
                    └─▶ KubernetesSandboxManager extracts threadId
                          │
                          └─▶ provider.getOrCreate({ threadId: "abc", ... })
                                │
                                ├─ (first run)   creates Pod/SandboxClaim
                                │                labels it thread-id=abc
                                │
                                └─ (later runs)  finds by label selector
                                                 reconnects → same sandbox
```

## Per-thread sandboxes and lifecycle management

### KubernetesSandboxManager (LangGraph integration)

```typescript
import { KubernetesSandboxManager } from "@bitkaio/langchain-kubernetes";

const manager = new KubernetesSandboxManager(
  {
    mode: "agent-sandbox",
    routerUrl: "http://sandbox-router-svc.default.svc.cluster.local:8080",
    templateName: "python-sandbox-template",
  },
  {
    ttlSeconds: 3600,         // absolute TTL from creation (seconds)
    ttlIdleSeconds: 1800,     // idle TTL from last execute() (seconds)
    defaultLabels: { project: "my-agent", env: "prod" },
  }
);

// Pass to LangGraph executor — thread_id is extracted from RunnableConfig automatically
const factory = manager.backendFactory;
const sandbox = await factory({ configurable: { thread_id: "conv-abc-123" } });

// Lookup without creating
const cached = manager.getSandbox("conv-abc-123"); // KubernetesSandbox | undefined

// Cleanup — async dispose (TypeScript 5.2+ "using" keyword)
await using manager = new KubernetesSandboxManager({ ... });

// Or manual:
await manager.shutdown();
```

**`KubernetesSandboxManagerOptions`:**

| Field | Type | Default | Description |
| ----- | ---- | ------- | ----------- |
| `ttlSeconds` | `number \| undefined` | — | Absolute TTL from creation (seconds) |
| `ttlIdleSeconds` | `number \| undefined` | — | Idle TTL from last `execute()` (seconds) |
| `defaultLabels` | `Record<string, string> \| undefined` | — | Labels applied to every sandbox (auto-prefixed) |

### Provider API — per-thread getOrCreate

```typescript
// Create new sandbox
const sandbox = await provider.getOrCreate();

// Idempotent — returns existing sandbox for this thread, or creates a new one
const sandbox = await provider.getOrCreate({
  threadId: "conv-abc-123",
  labels: { customer: "acme" },      // auto-prefixed with langchain-kubernetes.bitkaio.com/
  ttlSeconds: 3600,
  ttlIdleSeconds: 600,
});

// Reconnect to a specific sandbox by ID (backward-compatible string form)
const sandbox = await provider.getOrCreate("existing-id");

// Look up without creating
const existing = await provider.findByThreadId("conv-abc-123"); // undefined if not found

// List all managed sandboxes from the K8s API
const { sandboxes, cursor } = await provider.list();
const { sandboxes } = await provider.list({ threadId: "conv-abc-123" });
const { sandboxes } = await provider.list({ status: "running" });
const { sandboxes } = await provider.list({ labels: { customer: "acme" } });
// Pagination:
const page2 = await provider.list({ cursor });

// Operational methods
const result = await provider.cleanup();               // CleanupResult
const result = await provider.cleanup(300);            // override idle threshold (seconds)
const stats  = await provider.stats();                 // ProviderStats
const status = await provider.poolStatus();            // WarmPoolStatus

// Delete (idempotent)
await provider.delete(sandbox.id);
```

**`GetOrCreateOptions`:**

| Field | Type | Description |
| ----- | ---- | ----------- |
| `threadId` | `string \| undefined` | Thread/conversation identifier for label-based lookup |
| `labels` | `Record<string, string> \| undefined` | Per-call labels (auto-prefixed) |
| `ttlSeconds` | `number \| undefined` | Absolute TTL override (seconds) |
| `ttlIdleSeconds` | `number \| undefined` | Idle TTL override (seconds) |

**`SandboxListResponse`:**

```typescript
interface SandboxListResponse {
  sandboxes: SandboxInfo[];
  cursor?: string;  // Kubernetes continue token for pagination
}

interface SandboxInfo {
  id: string;
  namespace: string;
  threadId?: string;
  labels?: Record<string, string>;
  annotations?: Record<string, string>;
  createdAt?: string;    // ISO-8601 from langchain-kubernetes.bitkaio.com/created-at
  lastActivity?: string; // ISO-8601 from langchain-kubernetes.bitkaio.com/last-activity
  phase?: string;        // Kubernetes Pod phase
  status?: string;       // "running" | "warm" | "pending" | "terminated"
}
```

**`CleanupResult` / `ProviderStats` / `WarmPoolStatus`:**

```typescript
interface CleanupResult {
  deleted: string[];  // sandbox IDs that were deleted
  kept: number;       // sandboxes within their TTL / idle threshold
}

interface ProviderStats {
  total: number;
  running: number;
  warm: number;
  idle: number;       // running sandboxes past idleThresholdSeconds (default 300)
  threadIds: number;  // distinct thread IDs across all sandboxes
}

interface WarmPoolStatus {
  available: number;  // warm Pods ready to be claimed
  active: number;     // Pods currently assigned to a thread
  total: number;
  target: number;     // configured warmPoolSize
}
```

### New configuration options

The following fields were added to `KubernetesProviderConfig` (both modes unless noted):

| Field | Type | Default | Description |
| ----- | ---- | ------- | ----------- |
| `defaultLabels` | `Record<string, string> \| undefined` | — | Labels applied to every sandbox (auto-prefixed with `langchain-kubernetes.bitkaio.com/`) |
| `ttlSeconds` | `number \| undefined` | — | Default absolute TTL for `getOrCreate()` |
| `ttlIdleSeconds` | `number \| undefined` | — | Default idle TTL for `getOrCreate()` |
| `warmPoolSize` | `number \| undefined` | — | Pre-created warm Pods (raw mode only) |
| `warmPoolName` | `string \| undefined` | — | `SandboxWarmPool` resource name (agent-sandbox only) |

### Warm pool configuration

**agent-sandbox mode:**

```typescript
const provider = new KubernetesProvider({
  mode: "agent-sandbox",
  routerUrl: "http://sandbox-router-svc.default.svc.cluster.local:8080",
  templateName: "python-sandbox-template",
  warmPoolName: "python-pool",   // name of a SandboxWarmPool CRD in the cluster
});
```

**raw mode:**

```typescript
const provider = new KubernetesProvider({
  mode: "raw",
  warmPoolSize: 3,   // pre-create 3 idle Pods, replenish after each delete
});
```

See [`docs/warm-pool.yaml`](../docs/warm-pool.yaml) for cluster YAML examples.

## agent-sandbox mode — setup

### 1. Install the controller

Follow the [agent-sandbox installation guide](https://github.com/kubernetes-sigs/agent-sandbox).

### 2. Create a SandboxTemplate

```yaml
# examples/k8s/sandbox-template.yaml
apiVersion: extensions.agents.x-k8s.io/v1alpha1
kind: SandboxTemplate
metadata:
  name: python-sandbox-template
  namespace: default
spec:
  podTemplate:
    spec:
      containers:
      - name: python-runtime
        image: us-central1-docker.pkg.dev/k8s-staging-images/agent-sandbox/python-runtime-sandbox:latest-main
        ports:
        - containerPort: 8888
        readinessProbe:
          httpGet: { path: "/", port: 8888 }
          periodSeconds: 1
        resources:
          requests: { cpu: "250m", memory: "512Mi" }
      restartPolicy: "OnFailure"
```

```bash
kubectl apply -f examples/k8s/sandbox-template.yaml
```

### 3. Apply RBAC

```bash
kubectl apply -f examples/k8s/sandbox-router-rbac.yaml
```

### 4. Connect

**In-cluster** (the primary use case):

```typescript
const provider = new KubernetesProvider({
  routerUrl: "http://sandbox-router-svc.default.svc.cluster.local:8080",
  templateName: "python-sandbox-template",
});
```

**Local development** — the client needs access to both the sandbox-router and the Kubernetes API. Run two port-forwards:

```bash
# Forward the sandbox-router
kubectl port-forward svc/sandbox-router-svc 8080:8080 -n default

# Forward kubectl proxy for k8s API access
kubectl proxy --port=8001
```

```typescript
const provider = new KubernetesProvider({
  routerUrl: "http://localhost:8080",
  templateName: "python-sandbox-template",
  kubeApiUrl: "http://localhost:8001", // kubectl proxy — no auth needed
});
```

## raw mode — setup

### 1. Create the namespace

```bash
kubectl create namespace deepagents-sandboxes
```

### 2. Apply RBAC

```bash
kubectl apply -f examples/k8s/raw-mode-rbac.yaml
```

### 3. Configure

```typescript
const provider = new KubernetesProvider({
  mode: "raw",
  namespace: "deepagents-sandboxes",
  image: "python:3.12-slim",
  blockNetwork: true, // deny-all NetworkPolicy (requires NetworkPolicy-capable CNI)
});
```

For local kind clusters, `kindnet` does not support NetworkPolicy. Either use Calico or set `blockNetwork: false`.

## Error handling

```typescript
import {
  SandboxNotFoundError,
  SandboxStartupTimeoutError,
  SandboxRouterError,
  TemplateNotFoundError,
  MissingDependencyError,
} from "@bitkaio/langchain-kubernetes";

try {
  const sandbox = await provider.getOrCreate();
} catch (err) {
  if (err instanceof TemplateNotFoundError) {
    // SandboxTemplate doesn't exist in the cluster
  } else if (err instanceof SandboxStartupTimeoutError) {
    // Sandbox didn't become ready in time
  } else if (err instanceof SandboxRouterError) {
    // Router or Kubernetes API not reachable
  } else if (err instanceof MissingDependencyError) {
    // @kubernetes/client-node not installed (raw mode)
  }
}
```

## Integration tests

Integration tests require a running cluster:

```bash
# raw mode (any cluster)
K8S_INTEGRATION=1 npm run test:integration

# agent-sandbox mode (requires controller + CRDs)
K8S_INTEGRATION=1 SANDBOX_TEMPLATE=python-sandbox-template \
  ROUTER_URL=http://localhost:8080 npm run test:integration
```

## Requirements

- Node.js ≥ 18
- Kubernetes cluster (kind, GKE, EKS, AKS, OpenShift, …)
- For `agent-sandbox` mode: [`kubernetes-sigs/agent-sandbox`](https://github.com/kubernetes-sigs/agent-sandbox) controller
- For `raw` mode: `@kubernetes/client-node` + `tar-stream`; NetworkPolicy-capable CNI if `blockNetwork: true`
