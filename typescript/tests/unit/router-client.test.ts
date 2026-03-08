import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { SandboxRouterClient } from "../../src/router-client.js";
import {
  SandboxRouterError,
  SandboxStartupTimeoutError,
  TemplateNotFoundError,
} from "../../src/errors.js";

// ── Mock node:fs/promises so SA token reads don't hit disk ────────────────────

vi.mock("node:fs/promises", () => ({
  readFile: vi.fn().mockResolvedValue("test-sa-token"),
}));

// ── Helpers ───────────────────────────────────────────────────────────────────

const ROUTER_URL = "http://router.test:8080";
const K8S_API_URL = "http://localhost:8001";
const NAMESPACE = "default";
const SANDBOX_NAME = "sandbox-claim-abcd1234";
const TEMPLATE = "python-sandbox-template";

/** Build a router client pointed at a test kubectl proxy. */
function makeClient(opts?: Partial<Parameters<typeof SandboxRouterClient>[1]>): SandboxRouterClient {
  return new SandboxRouterClient(ROUTER_URL, {
    namespace: NAMESPACE,
    kubeApiUrl: K8S_API_URL,
    kubeToken: "test-token",
    serverPort: 8888,
    sandboxReadyTimeoutMs: 5_000,
    ...opts,
  });
}

/** Create a mock Response object. */
function mockResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => "application/json" } as unknown as Headers,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  } as unknown as Response;
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("SandboxRouterClient.run", () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, "fetch");
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it("POSTs to /execute with X-Sandbox-ID header", async () => {
    fetchSpy.mockResolvedValueOnce(
      mockResponse({ stdout: "hello\n", stderr: "", exit_code: 0 })
    );

    const client = makeClient();
    const result = await client.run(SANDBOX_NAME, "echo hello");

    expect(fetchSpy).toHaveBeenCalledOnce();
    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${ROUTER_URL}/execute`);
    expect(init.method).toBe("POST");
    const headers = init.headers as Record<string, string>;
    expect(headers["X-Sandbox-ID"]).toBe(SANDBOX_NAME);
    expect(headers["X-Sandbox-Namespace"]).toBe(NAMESPACE);
    expect(headers["X-Sandbox-Port"]).toBe("8888");

    expect(result.stdout).toBe("hello\n");
    expect(result.exitCode).toBe(0);
  });

  it("wraps command in sh -c for shell feature support", async () => {
    fetchSpy.mockResolvedValueOnce(
      mockResponse({ stdout: "", stderr: "", exit_code: 0 })
    );

    const client = makeClient();
    await client.run(SANDBOX_NAME, "echo hello && echo world");

    const [, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse(init.body as string) as { command: string };
    expect(body.command).toMatch(/^sh -c /);
    expect(body.command).toContain("echo hello && echo world");
  });

  it("throws SandboxRouterError on non-2xx response", async () => {
    fetchSpy.mockResolvedValueOnce(mockResponse({ detail: "Bad gateway" }, 502));

    const client = makeClient();
    await expect(client.run(SANDBOX_NAME, "ls")).rejects.toThrow(SandboxRouterError);
  });

  it("throws SandboxRouterError on network failure", async () => {
    fetchSpy.mockRejectedValueOnce(new Error("Connection refused"));

    const client = makeClient();
    await expect(client.run(SANDBOX_NAME, "ls")).rejects.toThrow(SandboxRouterError);
  });
});

describe("SandboxRouterClient.uploadFile", () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, "fetch");
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it("uses execute() to write file via base64", async () => {
    fetchSpy.mockResolvedValue(
      mockResponse({ stdout: "", stderr: "", exit_code: 0 })
    );

    const client = makeClient();
    const content = Buffer.from("hello world");
    await client.uploadFile(SANDBOX_NAME, "/workspace/test.txt", content);

    // Should have called execute (POSTed to /execute)
    expect(fetchSpy).toHaveBeenCalled();
    const [url] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${ROUTER_URL}/execute`);
  });

  it("throws SandboxRouterError on non-zero exit code", async () => {
    fetchSpy.mockResolvedValue(
      mockResponse({ stdout: "", stderr: "Permission denied", exit_code: 1 })
    );

    const client = makeClient();
    await expect(
      client.uploadFile(SANDBOX_NAME, "/workspace/test.txt", Buffer.from("x"))
    ).rejects.toThrow(SandboxRouterError);
  });
});

describe("SandboxRouterClient.downloadFile", () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, "fetch");
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it("uses execute() + base64 to read file", async () => {
    const fileContent = Buffer.from("hello world");
    const b64 = fileContent.toString("base64");

    fetchSpy.mockResolvedValue(
      mockResponse({ stdout: b64, stderr: "", exit_code: 0 })
    );

    const client = makeClient();
    const result = await client.downloadFile(SANDBOX_NAME, "/workspace/file.txt");

    expect(Buffer.from(result).toString()).toBe("hello world");
  });

  it("throws SandboxRouterError when file not found (non-zero exit)", async () => {
    fetchSpy.mockResolvedValue(
      mockResponse({ stdout: "", stderr: "No such file", exit_code: 1 })
    );

    const client = makeClient();
    await expect(
      client.downloadFile(SANDBOX_NAME, "/missing.txt")
    ).rejects.toThrow(SandboxRouterError);
  });
});

describe("SandboxRouterClient.createSandbox", () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, "fetch");
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it("creates SandboxClaim and polls until ready", async () => {
    // 1st call: POST SandboxClaim
    // 2nd call: GET Sandbox status (not ready)
    // 3rd call: GET Sandbox status (ready)
    fetchSpy
      .mockResolvedValueOnce(mockResponse({ metadata: { name: "sandbox-claim-1111" } })) // create claim
      .mockResolvedValueOnce(mockResponse({ status: 404 }, 404)) // first status poll (not found)
      .mockResolvedValueOnce(
        mockResponse({
          metadata: { name: "sandbox-claim-1111" },
          status: {
            conditions: [{ type: "Ready", status: "True" }],
          },
        })
      ); // ready

    const client = makeClient({ sandboxReadyTimeoutMs: 30_000 });
    const info = await client.createSandbox(TEMPLATE);

    expect(info.ready).toBe(true);
    expect(info.name).toBeTruthy();
  });

  it("throws TemplateNotFoundError when k8s returns 404 on create", async () => {
    fetchSpy.mockResolvedValueOnce(
      mockResponse({ message: "not found" }, 404)
    );

    const client = makeClient();
    await expect(client.createSandbox(TEMPLATE)).rejects.toThrow(TemplateNotFoundError);
  });

  it("throws SandboxStartupTimeoutError when sandbox never becomes ready", async () => {
    fetchSpy
      .mockResolvedValueOnce(mockResponse({ metadata: { name: "sandbox-claim-timeout" } })) // create
      .mockResolvedValue(
        mockResponse({
          metadata: { name: "sandbox-claim-timeout" },
          status: { conditions: [] },
        })
      ); // never ready

    const client = makeClient({ sandboxReadyTimeoutMs: 100 });
    await expect(client.createSandbox(TEMPLATE)).rejects.toThrow(SandboxStartupTimeoutError);
  });
});

describe("SandboxRouterClient.deleteSandbox", () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, "fetch");
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it("sends DELETE to k8s API for the claim", async () => {
    fetchSpy.mockResolvedValueOnce(mockResponse({}, 200));

    const client = makeClient();
    await client.deleteSandbox(SANDBOX_NAME);

    const [url, init] = fetchSpy.mock.calls[0] as [string, RequestInit];
    expect(url).toContain(SANDBOX_NAME);
    expect(init.method).toBe("DELETE");
  });

  it("is idempotent — 404 on delete is a no-op", async () => {
    fetchSpy.mockResolvedValueOnce(mockResponse({ message: "not found" }, 404));

    const client = makeClient();
    await expect(client.deleteSandbox(SANDBOX_NAME)).resolves.toBeUndefined();
  });
});

describe("SandboxRouterClient.listSandboxes", () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    fetchSpy = vi.spyOn(globalThis, "fetch");
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it("returns parsed sandbox list", async () => {
    fetchSpy.mockResolvedValueOnce(
      mockResponse({
        items: [
          { metadata: { name: "sandbox-claim-aaa" } },
          { metadata: { name: "sandbox-claim-bbb" } },
        ],
      })
    );

    const client = makeClient();
    const sandboxes = await client.listSandboxes();

    expect(sandboxes).toHaveLength(2);
    expect(sandboxes[0]?.name).toBe("sandbox-claim-aaa");
    expect(sandboxes[1]?.name).toBe("sandbox-claim-bbb");
  });

  it("returns empty array when no claims exist", async () => {
    fetchSpy.mockResolvedValueOnce(mockResponse({ items: [] }));

    const client = makeClient();
    const sandboxes = await client.listSandboxes();
    expect(sandboxes).toHaveLength(0);
  });
});
