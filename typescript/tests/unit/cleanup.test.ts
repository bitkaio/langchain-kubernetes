import { describe, it, expect, vi, afterEach } from "vitest";
import { KubernetesProvider } from "../../src/provider.js";
import {
  ANN_TTL_SECONDS,
  ANN_TTL_IDLE_SECONDS,
  ANN_CREATED_AT,
  ANN_LAST_ACTIVITY,
} from "../../src/labels.js";
import type { SandboxListResponse } from "../../src/provider.js";

// ── Helpers ────────────────────────────────────────────────────────────────────

function rawProvider() {
  return new KubernetesProvider({ mode: "raw" });
}

function agentSandboxProvider() {
  return new KubernetesProvider({
    mode: "agent-sandbox",
    routerUrl: "http://localhost:8080",
    templateName: "python",
  });
}

function msAgo(ms: number): string {
  return new Date(Date.now() - ms).toISOString();
}

function sandboxWithAnnotations(id: string, annotations: Record<string, string>): SandboxListResponse {
  return {
    sandboxes: [
      {
        id,
        namespace: "test-ns",
        annotations,
        status: "running",
      },
    ],
  };
}

// ── TTL Annotation Tests ───────────────────────────────────────────────────────

describe("cleanup() — TTL annotations", () => {
  afterEach(() => vi.restoreAllMocks());

  it("deletes sandbox that has exceeded absolute TTL", async () => {
    const provider = rawProvider();
    const createdAt = msAgo(7300 * 1000); // 7300s ago, TTL=7200s

    vi.spyOn(provider, "list").mockResolvedValue(sandboxWithAnnotations("expired-sb", {
      [ANN_TTL_SECONDS]: "7200",
      [ANN_CREATED_AT]: createdAt,
    }));
    const deleteSpy = vi.spyOn(provider, "delete").mockResolvedValue();

    const result = await provider.cleanup();
    expect(deleteSpy).toHaveBeenCalledWith("expired-sb");
    expect(result.deleted).toContain("expired-sb");
    expect(result.kept).toBe(0);
  });

  it("keeps sandbox within its absolute TTL", async () => {
    const provider = rawProvider();
    const createdAt = msAgo(3600 * 1000); // 3600s ago, TTL=7200s

    vi.spyOn(provider, "list").mockResolvedValue(sandboxWithAnnotations("fresh-sb", {
      [ANN_TTL_SECONDS]: "7200",
      [ANN_CREATED_AT]: createdAt,
    }));
    const deleteSpy = vi.spyOn(provider, "delete").mockResolvedValue();

    const result = await provider.cleanup();
    expect(deleteSpy).not.toHaveBeenCalled();
    expect(result.kept).toBe(1);
    expect(result.deleted).toHaveLength(0);
  });

  it("deletes sandbox that has exceeded idle TTL", async () => {
    const provider = rawProvider();
    const lastActivity = msAgo(1200 * 1000); // 1200s ago, idle TTL=600s

    vi.spyOn(provider, "list").mockResolvedValue(sandboxWithAnnotations("idle-sb", {
      [ANN_TTL_IDLE_SECONDS]: "600",
      [ANN_LAST_ACTIVITY]: lastActivity,
    }));
    const deleteSpy = vi.spyOn(provider, "delete").mockResolvedValue();

    const result = await provider.cleanup();
    expect(deleteSpy).toHaveBeenCalledWith("idle-sb");
    expect(result.deleted).toContain("idle-sb");
  });

  it("keeps sandbox within its idle TTL", async () => {
    const provider = rawProvider();
    const lastActivity = msAgo(100 * 1000); // 100s ago, idle TTL=600s

    vi.spyOn(provider, "list").mockResolvedValue(sandboxWithAnnotations("active-sb", {
      [ANN_TTL_IDLE_SECONDS]: "600",
      [ANN_LAST_ACTIVITY]: lastActivity,
    }));
    vi.spyOn(provider, "delete").mockResolvedValue();

    const result = await provider.cleanup();
    expect(result.kept).toBe(1);
    expect(result.deleted).toHaveLength(0);
  });

  it("uses created-at as fallback for idle check when last-activity absent", async () => {
    const provider = rawProvider();
    const createdAt = msAgo(1500 * 1000); // 1500s ago, idle TTL=600s

    vi.spyOn(provider, "list").mockResolvedValue(sandboxWithAnnotations("old-sb", {
      [ANN_TTL_IDLE_SECONDS]: "600",
      [ANN_CREATED_AT]: createdAt,
    }));
    const deleteSpy = vi.spyOn(provider, "delete").mockResolvedValue();

    const result = await provider.cleanup();
    expect(deleteSpy).toHaveBeenCalled();
    expect(result.deleted).toContain("old-sb");
  });

  it("cleanup(maxIdleSeconds) overrides per-sandbox annotation", async () => {
    const provider = rawProvider();
    const lastActivity = msAgo(400 * 1000); // 400s ago

    vi.spyOn(provider, "list").mockResolvedValue(sandboxWithAnnotations("sb", {
      [ANN_TTL_IDLE_SECONDS]: "600", // annotation says 600s — would be kept
      [ANN_LAST_ACTIVITY]: lastActivity,
    }));
    const deleteSpy = vi.spyOn(provider, "delete").mockResolvedValue();

    const result = await provider.cleanup(300); // override: 300s
    expect(deleteSpy).toHaveBeenCalledWith("sb");
    expect(result.deleted).toContain("sb");
  });

  it("handles empty sandbox list gracefully", async () => {
    const provider = rawProvider();
    vi.spyOn(provider, "list").mockResolvedValue({ sandboxes: [] });

    const result = await provider.cleanup();
    expect(result.deleted).toHaveLength(0);
    expect(result.kept).toBe(0);
  });

  it("continues when delete throws, counts remaining", async () => {
    const provider = rawProvider();
    const createdAt = msAgo(9000 * 1000);

    vi.spyOn(provider, "list").mockResolvedValue({
      sandboxes: [
        { id: "fail-sb", namespace: "ns", annotations: { [ANN_TTL_SECONDS]: "100", [ANN_CREATED_AT]: createdAt }, status: "running" },
        { id: "fresh-sb", namespace: "ns", annotations: {}, status: "running" },
      ],
    });
    vi.spyOn(provider, "delete").mockRejectedValue(new Error("k8s error"));

    const result = await provider.cleanup();
    expect(result.deleted).toHaveLength(0); // delete threw, not counted
    expect(result.kept).toBe(1);
  });
});

// ── stats() ────────────────────────────────────────────────────────────────────

describe("stats()", () => {
  afterEach(() => vi.restoreAllMocks());

  it("returns zeroes for empty list", async () => {
    const provider = rawProvider();
    vi.spyOn(provider, "list").mockResolvedValue({ sandboxes: [] });

    const s = await provider.stats();
    expect(s).toEqual({ total: 0, running: 0, warm: 0, idle: 0, threadIds: 0 });
  });

  it("counts running and warm sandboxes correctly", async () => {
    const provider = rawProvider();
    vi.spyOn(provider, "list").mockResolvedValue({
      sandboxes: [
        { id: "r1", namespace: "ns", status: "running", threadId: "t1" },
        { id: "r2", namespace: "ns", status: "running", threadId: "t2" },
        { id: "w1", namespace: "ns", status: "warm" },
      ],
    });

    const s = await provider.stats();
    expect(s.total).toBe(3);
    expect(s.running).toBe(2);
    expect(s.warm).toBe(1);
    expect(s.threadIds).toBe(2);
  });

  it("counts idle based on idleThresholdSeconds", async () => {
    const provider = rawProvider();
    const longAgo = msAgo(700 * 1000); // 700s ago > 300s threshold

    vi.spyOn(provider, "list").mockResolvedValue({
      sandboxes: [
        {
          id: "idle-r",
          namespace: "ns",
          status: "running",
          annotations: { [ANN_LAST_ACTIVITY]: longAgo },
        },
        {
          id: "active-r",
          namespace: "ns",
          status: "running",
          annotations: { [ANN_LAST_ACTIVITY]: msAgo(60 * 1000) },
        },
      ],
    });

    const s = await provider.stats(300);
    expect(s.idle).toBe(1);
  });
});
