import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { KubernetesSandboxManager, extractThreadId } from "../../src/manager.js";
import { KubernetesProvider } from "../../src/provider.js";
import { KubernetesSandbox } from "../../src/sandbox.js";

// ── Helpers ────────────────────────────────────────────────────────────────────

function rawConfig() {
  return { mode: "raw" as const };
}

function makeMockSandbox(id: string): KubernetesSandbox {
  return { id } as unknown as KubernetesSandbox;
}

function makeManager(options = {}) {
  return new KubernetesSandboxManager(rawConfig(), options);
}

// ── extractThreadId ────────────────────────────────────────────────────────────

describe("extractThreadId", () => {
  it("extracts from { configurable: { thread_id } }", () => {
    const result = extractThreadId({ configurable: { thread_id: "my-thread" } });
    expect(result).toBe("my-thread");
  });

  it("generates UUID when config is empty object", () => {
    const result = extractThreadId({});
    expect(result).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("generates UUID when configurable has no thread_id", () => {
    const result = extractThreadId({ configurable: { other_key: "value" } });
    expect(result).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("generates UUID when config is null", () => {
    const result = extractThreadId(null);
    expect(result).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("generates UUID when config is a string", () => {
    const result = extractThreadId("not-an-object");
    expect(result).toMatch(/^[0-9a-f-]{36}$/);
  });

  it("returns empty string thread_id as UUID (falsy guard)", () => {
    const result = extractThreadId({ configurable: { thread_id: "" } });
    expect(result).toMatch(/^[0-9a-f-]{36}$/);
  });
});

// ── backendFactory / abackendFactory ──────────────────────────────────────────

describe("KubernetesSandboxManager.backendFactory", () => {
  let manager: KubernetesSandboxManager;

  beforeEach(() => {
    manager = makeManager();
  });

  it("creates a sandbox for a thread_id", async () => {
    const mockSandbox = makeMockSandbox("sb-001");
    vi.spyOn(manager._provider, "getOrCreate").mockResolvedValue(mockSandbox);

    const factory = manager.backendFactory;
    const result = await factory({ configurable: { thread_id: "thread-1" } });
    expect(result.id).toBe("sb-001");
  });

  it("caches: same thread_id returns same sandbox, calls getOrCreate once", async () => {
    const mockSandbox = makeMockSandbox("sb-cached");
    const spy = vi.spyOn(manager._provider, "getOrCreate").mockResolvedValue(mockSandbox);

    const factory = manager.backendFactory;
    const r1 = await factory({ configurable: { thread_id: "same-thread" } });
    const r2 = await factory({ configurable: { thread_id: "same-thread" } });

    expect(r1).toBe(r2);
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("different thread_ids produce different sandboxes", async () => {
    const sb1 = makeMockSandbox("sb-thread-1");
    const sb2 = makeMockSandbox("sb-thread-2");

    vi.spyOn(manager._provider, "getOrCreate")
      .mockResolvedValueOnce(sb1)
      .mockResolvedValueOnce(sb2);

    const factory = manager.backendFactory;
    const r1 = await factory({ configurable: { thread_id: "thread-1" } });
    const r2 = await factory({ configurable: { thread_id: "thread-2" } });

    expect(r1.id).toBe("sb-thread-1");
    expect(r2.id).toBe("sb-thread-2");
    expect(r1).not.toBe(r2);
  });

  it("passes ttl_seconds and ttl_idle_seconds to getOrCreate", async () => {
    const managerWithTtl = new KubernetesSandboxManager(rawConfig(), {
      ttlSeconds: 3600,
      ttlIdleSeconds: 600,
    });
    const mockSandbox = makeMockSandbox("ttl-sb");
    const spy = vi.spyOn(managerWithTtl._provider, "getOrCreate").mockResolvedValue(mockSandbox);

    await managerWithTtl.abackendFactory({ configurable: { thread_id: "ttl-thread" } });

    expect(spy).toHaveBeenCalledWith(expect.objectContaining({
      ttlSeconds: 3600,
      ttlIdleSeconds: 600,
    }));
  });

  it("passes defaultLabels to getOrCreate", async () => {
    const managerWithLabels = new KubernetesSandboxManager(rawConfig(), {
      defaultLabels: { env: "test" },
    });
    const mockSandbox = makeMockSandbox("label-sb");
    const spy = vi.spyOn(managerWithLabels._provider, "getOrCreate").mockResolvedValue(mockSandbox);

    await managerWithLabels.abackendFactory({ configurable: { thread_id: "label-thread" } });

    expect(spy).toHaveBeenCalledWith(expect.objectContaining({
      labels: { env: "test" },
    }));
  });

  it("generates UUID thread_id when config has none", async () => {
    const mockSandbox = makeMockSandbox("uuid-sb");
    const spy = vi.spyOn(manager._provider, "getOrCreate").mockResolvedValue(mockSandbox);

    const result = await manager.abackendFactory({});
    expect(result).toBeDefined();
    const callArgs = spy.mock.calls[0][0] as { threadId: string };
    expect(callArgs.threadId).toMatch(/^[0-9a-f-]{36}$/);
  });
});

// ── getSandbox ─────────────────────────────────────────────────────────────────

describe("KubernetesSandboxManager.getSandbox", () => {
  it("returns cached sandbox for a known thread_id", async () => {
    const manager = makeManager();
    const mockSandbox = makeMockSandbox("cached");
    vi.spyOn(manager._provider, "getOrCreate").mockResolvedValue(mockSandbox);

    await manager.abackendFactory({ configurable: { thread_id: "known-thread" } });
    expect(manager.getSandbox("known-thread")).toBe(mockSandbox);
  });

  it("returns undefined for an unknown thread_id", () => {
    const manager = makeManager();
    expect(manager.getSandbox("unknown")).toBeUndefined();
  });
});

// ── shutdown ───────────────────────────────────────────────────────────────────

describe("KubernetesSandboxManager.shutdown", () => {
  it("deletes all cached sandboxes", async () => {
    const manager = makeManager();
    const sb1 = makeMockSandbox("sb-1");
    const sb2 = makeMockSandbox("sb-2");

    vi.spyOn(manager._provider, "getOrCreate")
      .mockResolvedValueOnce(sb1)
      .mockResolvedValueOnce(sb2);

    await manager.abackendFactory({ configurable: { thread_id: "t1" } });
    await manager.abackendFactory({ configurable: { thread_id: "t2" } });

    const deletedIds: string[] = [];
    vi.spyOn(manager._provider, "delete").mockImplementation(async (id) => {
      deletedIds.push(id);
    });

    await manager.shutdown();
    expect(new Set(deletedIds)).toEqual(new Set(["sb-1", "sb-2"]));
  });

  it("clears the internal cache after shutdown", async () => {
    const manager = makeManager();
    const mockSb = makeMockSandbox("sb-1");
    vi.spyOn(manager._provider, "getOrCreate").mockResolvedValue(mockSb);
    vi.spyOn(manager._provider, "delete").mockResolvedValue();

    await manager.abackendFactory({ configurable: { thread_id: "t1" } });
    expect(manager.getSandbox("t1")).toBeDefined();

    await manager.shutdown();
    expect(manager.getSandbox("t1")).toBeUndefined();
  });

  it("continues shutdown on individual delete errors", async () => {
    const manager = makeManager();
    const sb1 = makeMockSandbox("fail-sb");
    vi.spyOn(manager._provider, "getOrCreate").mockResolvedValue(sb1);
    vi.spyOn(manager._provider, "delete").mockRejectedValue(new Error("delete failed"));

    await manager.abackendFactory({ configurable: { thread_id: "t-err" } });
    await expect(manager.shutdown()).resolves.toBeUndefined(); // no throw
  });

  it("Symbol.asyncDispose calls shutdown", async () => {
    const manager = makeManager();
    const spy = vi.spyOn(manager, "shutdown").mockResolvedValue();
    await manager[Symbol.asyncDispose]();
    expect(spy).toHaveBeenCalledOnce();
  });
});

// ── Cleanup ────────────────────────────────────────────────────────────────────

afterEach(() => {
  vi.restoreAllMocks();
});
