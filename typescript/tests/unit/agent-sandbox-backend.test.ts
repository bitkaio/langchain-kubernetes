import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { AgentSandboxBackend } from "../../src/backends/agent-sandbox.js";
import type { KubernetesProviderConfig } from "../../src/config.js";
import { resolveConfig } from "../../src/config.js";

// ── Mock node:fs/promises ─────────────────────────────────────────────────────

vi.mock("node:fs/promises", () => ({
  readFile: vi.fn().mockResolvedValue("test-sa-token"),
}));

// ── Test helpers ──────────────────────────────────────────────────────────────

function makeConfig(
  overrides?: Partial<KubernetesProviderConfig>
): KubernetesProviderConfig {
  return resolveConfig({
    mode: "agent-sandbox",
    routerUrl: "http://router.test:8080",
    templateName: "python-sandbox-template",
    namespace: "default",
    kubeApiUrl: "http://localhost:8001",
    kubeToken: "test-token",
    ...overrides,
  });
}

function mockResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "application/json" } as unknown as Headers,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  } as unknown as Response;
}

const SANDBOX_NAME = "sandbox-claim-abcd1234";

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("AgentSandboxBackend.execute", () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, "fetch");
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it("returns combined output and exitCode", async () => {
    fetchSpy.mockResolvedValueOnce(
      mockResponse({ stdout: "hello\n", stderr: "warn\n", exit_code: 0 })
    );

    const backend = await AgentSandboxBackend.reconnect(SANDBOX_NAME, makeConfig());
    const result = await backend.execute("echo hello");

    expect(result.output).toBe("hello\nwarn\n");
    expect(result.exitCode).toBe(0);
    expect(result.truncated).toBe(false);
  });

  it("returns non-zero exit code from sandbox runtime", async () => {
    fetchSpy.mockResolvedValueOnce(
      mockResponse({ stdout: "", stderr: "not found", exit_code: 127 })
    );

    const backend = await AgentSandboxBackend.reconnect(SANDBOX_NAME, makeConfig());
    const result = await backend.execute("bogus-cmd");

    expect(result.exitCode).toBe(127);
    expect(result.output).toContain("not found");
  });
});

describe("AgentSandboxBackend.uploadFiles", () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, "fetch");
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it("returns success results for each uploaded file", async () => {
    fetchSpy.mockResolvedValue(
      mockResponse({ stdout: "", stderr: "", exit_code: 0 })
    );

    const backend = await AgentSandboxBackend.reconnect(SANDBOX_NAME, makeConfig());
    const results = await backend.uploadFiles([
      ["/workspace/a.txt", Buffer.from("content-a")],
      ["/workspace/b.txt", Buffer.from("content-b")],
    ]);

    expect(results).toHaveLength(2);
    expect(results[0]?.error).toBeNull();
    expect(results[1]?.error).toBeNull();
    expect(results[0]?.path).toBe("/workspace/a.txt");
  });

  it("returns error results when upload fails", async () => {
    fetchSpy.mockResolvedValue(
      mockResponse({ stdout: "", stderr: "Permission denied", exit_code: 1 })
    );

    const backend = await AgentSandboxBackend.reconnect(SANDBOX_NAME, makeConfig());
    const results = await backend.uploadFiles([
      ["/workspace/fail.txt", Buffer.from("x")],
    ]);

    expect(results[0]?.error).not.toBeNull();
  });
});

describe("AgentSandboxBackend.downloadFiles", () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, "fetch");
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it("returns file content when download succeeds", async () => {
    const fileContent = "hello from sandbox";
    const b64 = Buffer.from(fileContent).toString("base64");

    fetchSpy.mockResolvedValue(
      mockResponse({ stdout: b64, stderr: "", exit_code: 0 })
    );

    const backend = await AgentSandboxBackend.reconnect(SANDBOX_NAME, makeConfig());
    const results = await backend.downloadFiles(["/workspace/file.txt"]);

    expect(results[0]?.error).toBeNull();
    expect(Buffer.from(results[0]!.content!).toString()).toBe(fileContent);
  });

  it("returns file_not_found error when file is missing", async () => {
    // The SandboxRouterClient.downloadFile throws when exit code != 0
    fetchSpy.mockResolvedValue(
      mockResponse({ stdout: "", stderr: "No such file", exit_code: 1 })
    );

    const backend = await AgentSandboxBackend.reconnect(SANDBOX_NAME, makeConfig());
    const results = await backend.downloadFiles(["/missing.txt"]);

    expect(results[0]?.error).toBe("file_not_found");
    expect(results[0]?.content).toBeNull();
  });
});

describe("AgentSandboxBackend.cleanup", () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, "fetch");
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it("deletes the SandboxClaim", async () => {
    fetchSpy.mockResolvedValueOnce(mockResponse({}, 200));

    const backend = await AgentSandboxBackend.reconnect(SANDBOX_NAME, makeConfig());
    await backend.cleanup();

    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain(SANDBOX_NAME);
    expect((init as RequestInit).method).toBe("DELETE");
  });
});

describe("AgentSandboxBackend.id", () => {
  it("uses the sandbox name as id", async () => {
    const backend = await AgentSandboxBackend.reconnect(SANDBOX_NAME, makeConfig());
    expect(backend.id).toBe(SANDBOX_NAME);
  });
});
