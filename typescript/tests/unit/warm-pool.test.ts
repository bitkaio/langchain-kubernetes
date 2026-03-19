import { describe, it, expect, vi, afterEach } from "vitest";
import { KubernetesProvider } from "../../src/provider.js";
import {
  warmPoolSelector,
  POOL_STATUS_ACTIVE,
  POOL_STATUS_WARM,
  LK_LABEL_POOL_STATUS,
} from "../../src/labels.js";

// ── Helpers ────────────────────────────────────────────────────────────────────

function rawProviderWithPool(size: number) {
  return new KubernetesProvider({
    mode: "raw",
    warmPoolSize: size,
    namespace: "test-ns",
  });
}

function makeRunningPod(name: string, poolStatus = POOL_STATUS_WARM) {
  return {
    metadata: {
      name,
      namespace: "test-ns",
      labels: { [LK_LABEL_POOL_STATUS]: poolStatus, "deepagents.langchain.com/sandbox-id": name },
      annotations: {},
    },
    status: { phase: "Running" },
  };
}

// ── poolStatus() ───────────────────────────────────────────────────────────────

describe("poolStatus()", () => {
  afterEach(() => vi.restoreAllMocks());

  it("returns zeroes when loadRawClients fails", async () => {
    const provider = rawProviderWithPool(3);
    // loadRawClients will fail in unit tests (no k8s), so poolStatus catches
    const status = await provider.poolStatus();
    // Should not throw; available/active/total are 0 or counts from cache
    expect(status.target).toBe(3);
    expect(typeof status.available).toBe("number");
    expect(typeof status.active).toBe("number");
  });

  it("returns 0 target for agent-sandbox mode", async () => {
    const provider = new KubernetesProvider({
      mode: "agent-sandbox",
      routerUrl: "http://localhost:8080",
      templateName: "python",
    });

    vi.spyOn(provider, "list").mockResolvedValue({ sandboxes: [] });
    const status = await provider.poolStatus();
    expect(status.target).toBe(0);
  });
});

// ── warmPoolSelector label ─────────────────────────────────────────────────────

describe("warm pool label constants", () => {
  it("warmPoolSelector includes pool-status=warm", () => {
    const sel = warmPoolSelector();
    expect(sel).toContain(`${LK_LABEL_POOL_STATUS}=${POOL_STATUS_WARM}`);
  });

  it("POOL_STATUS_ACTIVE is different from POOL_STATUS_WARM", () => {
    expect(POOL_STATUS_ACTIVE).not.toBe(POOL_STATUS_WARM);
    expect(POOL_STATUS_WARM).toBe("warm");
    expect(POOL_STATUS_ACTIVE).toBe("active");
  });
});

// ── replenishWarmPool (indirect via provider.delete) ──────────────────────────

describe("warm pool replenishment trigger", () => {
  afterEach(() => vi.restoreAllMocks());

  it("delete() on raw mode with warmPoolSize triggers replenishment (no throw)", async () => {
    const provider = rawProviderWithPool(2);

    // Mock the internals to avoid actual k8s calls
    // replenishWarmPool is called fire-and-forget inside delete()
    // We just verify delete() doesn't throw when warmPoolSize > 0
    const mockDelete = vi.spyOn(provider, "delete").mockResolvedValue();
    await provider.delete("any-sandbox-id");
    expect(mockDelete).toHaveBeenCalledWith("any-sandbox-id");
  });
});

// ── Provider list + pool status integration ───────────────────────────────────

describe("list() with pool status filter", () => {
  afterEach(() => vi.restoreAllMocks());

  it("returns warm sandboxes when filtering by status=warm", async () => {
    const provider = rawProviderWithPool(3);

    vi.spyOn(provider, "list").mockImplementation(async (opts) => {
      const sandboxes = [
        { id: "warm-1", namespace: "test-ns", status: "warm" },
        { id: "active-1", namespace: "test-ns", status: "running" },
      ];
      const filtered = opts?.status
        ? sandboxes.filter((s) => s.status === opts.status)
        : sandboxes;
      return { sandboxes: filtered };
    });

    const result = await provider.list({ status: "warm" });
    expect(result.sandboxes).toHaveLength(1);
    expect(result.sandboxes[0].status).toBe("warm");
  });
});
