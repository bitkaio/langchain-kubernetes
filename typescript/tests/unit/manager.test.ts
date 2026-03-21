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
