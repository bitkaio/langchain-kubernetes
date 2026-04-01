import { describe, it, expect, vi, afterEach } from "vitest";
import { KubernetesSandboxManager } from "../../src/manager.js";
import { KubernetesSandbox } from "../../src/sandbox.js";

// ── Helpers ────────────────────────────────────────────────────────────────────

function rawConfig() {
  return { mode: "raw" as const };
}

function makeMockSandbox(id: string): KubernetesSandbox {
  return { id } as unknown as KubernetesSandbox;
}

function makeManager(options: Record<string, unknown> = {}) {
  return new KubernetesSandboxManager(rawConfig(), options);
}

/** Build a fake AsyncLocalStorageProviderSingleton that returns the given config. */
function fakeStorage(config: Record<string, unknown> | undefined) {
  return {
    AsyncLocalStorageProviderSingleton: {
      getInstance: () => ({ getStore: () => config }),
    },
  };
}

afterEach(() => vi.restoreAllMocks());

// ── getOrReconnect ─────────────────────────────────────────────────────────────

describe("KubernetesSandboxManager.getOrReconnect", () => {
  it("delegates to provider.getOrCreate with the given sandboxId", async () => {
    const manager = makeManager();
    const mockSandbox = makeMockSandbox("sb-001");
    const spy = vi.spyOn(manager._provider, "getOrCreate").mockResolvedValue(mockSandbox);

    const result = await manager.getOrReconnect("sb-001");

    expect(spy).toHaveBeenCalledWith(expect.objectContaining({ sandboxId: "sb-001" }));
    expect(result.id).toBe("sb-001");
  });

  it("passes undefined sandboxId when none given (new sandbox)", async () => {
    const manager = makeManager();
    const mockSandbox = makeMockSandbox("new-sb");
    const spy = vi.spyOn(manager._provider, "getOrCreate").mockResolvedValue(mockSandbox);

    await manager.getOrReconnect(undefined);

    expect(spy).toHaveBeenCalledWith(expect.objectContaining({ sandboxId: undefined }));
  });

  it("forwards ttlSeconds and ttlIdleSeconds to provider", async () => {
    const manager = new KubernetesSandboxManager(rawConfig(), {
      ttlSeconds: 3600,
      ttlIdleSeconds: 600,
    });
    const mockSandbox = makeMockSandbox("ttl-sb");
    const spy = vi.spyOn(manager._provider, "getOrCreate").mockResolvedValue(mockSandbox);

    await manager.getOrReconnect(undefined);

    expect(spy).toHaveBeenCalledWith(expect.objectContaining({
      ttlSeconds: 3600,
      ttlIdleSeconds: 600,
    }));
  });

  it("forwards defaultLabels to provider", async () => {
    const manager = new KubernetesSandboxManager(rawConfig(), {
      defaultLabels: { env: "prod" },
    });
    const mockSandbox = makeMockSandbox("label-sb");
    const spy = vi.spyOn(manager._provider, "getOrCreate").mockResolvedValue(mockSandbox);

    await manager.getOrReconnect(undefined);

    expect(spy).toHaveBeenCalledWith(expect.objectContaining({
      labels: { env: "prod" },
    }));
  });
});

// ── createAgentNode ────────────────────────────────────────────────────────────

describe("KubernetesSandboxManager.createAgentNode", () => {
  it("returns a function", () => {
    const manager = makeManager();
    const node = manager.createAgentNode({} as never);
    expect(typeof node).toBe("function");
  });

  it("accepts a custom stateSandboxKey option", () => {
    const manager = makeManager();
    const node = manager.createAgentNode({} as never, { stateSandboxKey: "myKey" });
    expect(typeof node).toBe("function");
  });
});

// ── shutdown ───────────────────────────────────────────────────────────────────

describe("KubernetesSandboxManager.shutdown", () => {
  it("calls provider.cleanup()", async () => {
    const manager = makeManager();
    const spy = vi.spyOn(manager._provider, "cleanup").mockResolvedValue({ deleted: [], kept: 0 });

    await manager.shutdown();

    expect(spy).toHaveBeenCalledOnce();
  });

  it("does not throw when cleanup fails", async () => {
    const manager = makeManager();
    vi.spyOn(manager._provider, "cleanup").mockRejectedValue(new Error("k8s error"));

    await expect(manager.shutdown()).resolves.toBeUndefined();
  });

  it("Symbol.asyncDispose calls shutdown", async () => {
    const manager = makeManager();
    const spy = vi.spyOn(manager, "shutdown").mockResolvedValue();

    await manager[Symbol.asyncDispose]();

    expect(spy).toHaveBeenCalledOnce();
  });
});

// ── _makeBackendFactory ────────────────────────────────────────────────────────

describe("KubernetesSandboxManager._makeBackendFactory", () => {
  it("throws when thread_id is absent from config", async () => {
    const manager = makeManager();
    vi.doMock("@langchain/core/singletons", () => fakeStorage({}));
    const factory = await manager._makeBackendFactory();
    expect(() => factory(null)).toThrow("no thread_id");
    vi.doUnmock("@langchain/core/singletons");
  });

  it("throws when sandbox not cached for the thread", async () => {
    const manager = makeManager();
    vi.doMock("@langchain/core/singletons", () =>
      fakeStorage({ configurable: { thread_id: "t-unknown" } })
    );
    const factory = await manager._makeBackendFactory();
    expect(() => factory(null)).toThrow("t-unknown");
    vi.doUnmock("@langchain/core/singletons");
  });

  it("returns the cached sandbox for the current thread", async () => {
    const manager = makeManager();
    const mockSandbox = makeMockSandbox("cached-sb");
    manager._sandboxByThread.set("t-42", mockSandbox);

    vi.doMock("@langchain/core/singletons", () =>
      fakeStorage({ configurable: { thread_id: "t-42" } })
    );
    const factory = await manager._makeBackendFactory();
    expect(factory(null)).toBe(mockSandbox);
    vi.doUnmock("@langchain/core/singletons");
  });

  it("does not call provider when sandbox is already cached", async () => {
    const manager = makeManager();
    const mockSandbox = makeMockSandbox("cached-sb");
    manager._sandboxByThread.set("t-42", mockSandbox);
    const spy = vi.spyOn(manager._provider, "getOrCreate");

    vi.doMock("@langchain/core/singletons", () =>
      fakeStorage({ configurable: { thread_id: "t-42" } })
    );
    const factory = await manager._makeBackendFactory();
    factory(null);
    expect(spy).not.toHaveBeenCalled();
    vi.doUnmock("@langchain/core/singletons");
  });
});

// ── createSetupNode ────────────────────────────────────────────────────────────

describe("KubernetesSandboxManager.createSetupNode", () => {
  it("throws when thread_id is missing from config", async () => {
    const manager = makeManager();
    vi.spyOn(manager._provider, "getOrCreate").mockResolvedValue(makeMockSandbox("sb-1"));
    const node = manager.createSetupNode();
    await expect(node({ sandboxId: undefined }, {})).rejects.toThrow("no thread_id");
  });

  it("populates _sandboxByThread and returns sandboxId update for new sandbox", async () => {
    const manager = makeManager();
    const mockSandbox = makeMockSandbox("new-sb");
    vi.spyOn(manager._provider, "getOrCreate").mockResolvedValue(mockSandbox);

    const node = manager.createSetupNode();
    const updates = await node(
      { sandboxId: undefined },
      { configurable: { thread_id: "thread-new" } }
    );

    expect(manager._sandboxByThread.get("thread-new")).toBe(mockSandbox);
    expect(updates["sandboxId"]).toBe("new-sb");
  });

  it("populates _sandboxByThread and returns empty updates when sandbox unchanged", async () => {
    const manager = makeManager();
    const mockSandbox = makeMockSandbox("existing-sb");
    vi.spyOn(manager._provider, "getOrCreate").mockResolvedValue(mockSandbox);

    const node = manager.createSetupNode();
    const updates = await node(
      { sandboxId: "existing-sb" },
      { configurable: { thread_id: "thread-existing" } }
    );

    expect(manager._sandboxByThread.get("thread-existing")).toBe(mockSandbox);
    expect(updates).toEqual({});
  });

  it("respects a custom stateSandboxKey", async () => {
    const manager = makeManager();
    const mockSandbox = makeMockSandbox("sb-custom");
    vi.spyOn(manager._provider, "getOrCreate").mockResolvedValue(mockSandbox);

    const node = manager.createSetupNode({ stateSandboxKey: "mySandbox" });
    const updates = await node(
      { mySandbox: undefined },
      { configurable: { thread_id: "t-custom" } }
    );

    expect(updates["mySandbox"]).toBe("sb-custom");
  });
});

// ── _ensureSandbox ────────────────────────────────────────────────────────────

describe("KubernetesSandboxManager._ensureSandbox", () => {
  it("acquires sandbox when not cached", async () => {
    const manager = makeManager();
    const mockSandbox = makeMockSandbox("new-sb");
    vi.spyOn(manager._provider, "getOrCreate").mockResolvedValue(mockSandbox);

    await manager._ensureSandbox("thread-1");

    expect(manager._sandboxByThread.get("thread-1")).toBe(mockSandbox);
  });

  it("is a no-op when sandbox already cached", async () => {
    const manager = makeManager();
    const existingSandbox = makeMockSandbox("existing-sb");
    manager._sandboxByThread.set("thread-1", existingSandbox);
    const spy = vi.spyOn(manager._provider, "getOrCreate");

    await manager._ensureSandbox("thread-1");

    expect(spy).not.toHaveBeenCalled();
    expect(manager._sandboxByThread.get("thread-1")).toBe(existingSandbox);
  });
});

// ── createAgent — returns proxied deepagent ──────────────────────────────────

describe("KubernetesSandboxManager.createAgent", () => {
  it("returns a proxy wrapping the deepagent (not a StateGraph)", async () => {
    const manager = makeManager();
    const mockAgent = { type: "deepagent", invoke: vi.fn() };
    const mockCreateDeepAgent = vi.fn().mockReturnValue(mockAgent);

    vi.doMock("deepagents", () => ({ createDeepAgent: mockCreateDeepAgent }));
    vi.doMock("@langchain/core/singletons", () => fakeStorage(undefined));

    const result = await manager.createAgent({} as never);

    // It's a proxy — accessing non-method properties passes through
    expect((result as Record<string, unknown>)["type"]).toBe("deepagent");
    expect(mockCreateDeepAgent).toHaveBeenCalledOnce();

    vi.doUnmock("deepagents");
    vi.doUnmock("@langchain/core/singletons");
  });

  it("passes backend factory and checkpointer to createDeepAgent", async () => {
    const manager = makeManager();
    const mockAgent = { type: "deepagent" };
    const mockCreateDeepAgent = vi.fn().mockReturnValue(mockAgent);
    const mockCheckpointer = { type: "checkpointer" };

    vi.doMock("deepagents", () => ({ createDeepAgent: mockCreateDeepAgent }));
    vi.doMock("@langchain/core/singletons", () => fakeStorage(undefined));

    await manager.createAgent({} as never, { checkpointer: mockCheckpointer });

    const callArgs = mockCreateDeepAgent.mock.calls[0][1];
    expect(callArgs.backend).toBeDefined();
    expect(typeof callArgs.backend).toBe("function");
    expect(callArgs.checkpointer).toBe(mockCheckpointer);

    vi.doUnmock("deepagents");
    vi.doUnmock("@langchain/core/singletons");
  });

  it("forwards extra options to createDeepAgent", async () => {
    const manager = makeManager();
    const mockCreateDeepAgent = vi.fn().mockReturnValue({});

    vi.doMock("deepagents", () => ({ createDeepAgent: mockCreateDeepAgent }));
    vi.doMock("@langchain/core/singletons", () => fakeStorage(undefined));

    await manager.createAgent({} as never, { systemPrompt: "Be helpful" });

    const callArgs = mockCreateDeepAgent.mock.calls[0][1];
    expect(callArgs.systemPrompt).toBe("Be helpful");

    vi.doUnmock("deepagents");
    vi.doUnmock("@langchain/core/singletons");
  });

  it("invoke wrapper calls _ensureSandbox before delegating", async () => {
    const manager = makeManager();
    const mockInvoke = vi.fn().mockResolvedValue({ messages: [] });
    const mockAgent = { invoke: mockInvoke };
    const mockCreateDeepAgent = vi.fn().mockReturnValue(mockAgent);

    vi.doMock("deepagents", () => ({ createDeepAgent: mockCreateDeepAgent }));
    vi.doMock("@langchain/core/singletons", () => fakeStorage(undefined));

    const spy = vi.spyOn(manager, "_ensureSandbox").mockResolvedValue();
    const agent = await manager.createAgent({} as never);

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    await (agent as any).invoke(
      { messages: [] },
      { configurable: { thread_id: "t-1" } }
    );

    expect(spy).toHaveBeenCalledWith("t-1");
    expect(mockInvoke).toHaveBeenCalledOnce();

    vi.doUnmock("deepagents");
    vi.doUnmock("@langchain/core/singletons");
  });
});

// ── cleanup ────────────────────────────────────────────────────────────────────

describe("KubernetesSandboxManager.cleanup", () => {
  it("delegates to provider.cleanup()", async () => {
    const manager = makeManager();
    const mockResult = { deleted: ["sb-1"], kept: 0 };
    const spy = vi.spyOn(manager._provider, "cleanup").mockResolvedValue(mockResult);

    const result = await manager.cleanup();

    expect(spy).toHaveBeenCalledOnce();
    expect(result).toBe(mockResult);
  });

  it("passes maxIdleSeconds to provider.cleanup()", async () => {
    const manager = makeManager();
    const spy = vi.spyOn(manager._provider, "cleanup").mockResolvedValue({ deleted: [], kept: 0 });

    await manager.cleanup(600);

    expect(spy).toHaveBeenCalledWith(600);
  });
});
