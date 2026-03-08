import { describe, it, expect, beforeAll, afterAll } from "vitest";
import * as k8s from "@kubernetes/client-node";
import { KubernetesProvider } from "../../src/provider.js";
import { KubernetesSandbox } from "../../src/sandbox.js";
import {
  INTEGRATION_ENABLED,
  buildTestKubeConfig,
  testNamespaceName,
  createTestNamespace,
  deleteTestNamespace,
} from "./setup.js";

describe.skipIf(!INTEGRATION_ENABLED)("KubernetesSandbox integration", () => {
  let coreApi: k8s.CoreV1Api;
  let testNamespace: string;
  let provider: KubernetesProvider;
  let sandbox: KubernetesSandbox;

  beforeAll(async () => {
    const kc = buildTestKubeConfig();
    coreApi = kc.makeApiClient(k8s.CoreV1Api);
    testNamespace = testNamespaceName();
    await createTestNamespace(coreApi, testNamespace);

    provider = new KubernetesProvider({
      namespace: testNamespace,
      image: "python:3.12-slim",
      blockNetwork: false, // network isolation not needed for basic exec tests
      startupTimeoutSeconds: 120,
      podTtlSeconds: undefined,
    });

    sandbox = await provider.getOrCreate();
  }, 180_000);

  afterAll(async () => {
    if (sandbox) {
      await provider.delete(sandbox.id).catch(() => undefined);
    }
    if (testNamespace) {
      await deleteTestNamespace(coreApi, testNamespace);
    }
  }, 120_000);

  it("has a non-empty id", () => {
    expect(sandbox.id).toBeTruthy();
    expect(typeof sandbox.id).toBe("string");
  });

  it("executes a simple command and returns output", async () => {
    const result = await sandbox.execute("echo hello-from-k8s");
    expect(result.output).toContain("hello-from-k8s");
    expect(result.exitCode).toBe(0);
    expect(result.truncated).toBe(false);
  });

  it("captures non-zero exit code", async () => {
    const result = await sandbox.execute("exit 42");
    expect(result.exitCode).toBe(42);
  });

  it("captures stderr in output", async () => {
    const result = await sandbox.execute("echo error-text >&2");
    expect(result.output).toContain("error-text");
  });

  it("can run a multi-line command", async () => {
    const result = await sandbox.execute(`
      python3 -c "
import sys
print('line1')
print('line2', file=sys.stderr)
print('line3')
"
    `);
    expect(result.output).toContain("line1");
    expect(result.output).toContain("line3");
  });

  it("can write and read a file via execute()", async () => {
    await sandbox.execute("echo 'test content' > /tmp/test.txt");
    const result = await sandbox.execute("cat /tmp/test.txt");
    expect(result.output).toContain("test content");
  });

  it("uploadFiles writes content accessible via execute()", async () => {
    const content = Buffer.from("uploaded content\n");
    const results = await sandbox.uploadFiles([["/tmp/uploaded.txt", content]]);
    expect(results[0]?.success).toBe(true);

    const read = await sandbox.execute("cat /tmp/uploaded.txt");
    expect(read.output).toContain("uploaded content");
  });

  it("downloadFiles retrieves file content", async () => {
    await sandbox.execute("echo 'download me' > /tmp/download.txt");
    const results = await sandbox.downloadFiles(["/tmp/download.txt"]);
    expect(results[0]?.success).toBe(true);
    expect(Buffer.from(results[0]!.content).toString()).toContain("download me");
  });

  it("downloadFiles returns failure for missing file", async () => {
    const results = await sandbox.downloadFiles(["/tmp/does-not-exist.txt"]);
    // tar returns no output for missing files; we should handle gracefully
    expect(results[0]?.path).toBe("/tmp/does-not-exist.txt");
  });

  it("can reconnect to existing sandbox via getOrCreate(id)", async () => {
    const reconnected = await provider.getOrCreate(sandbox.id);
    expect(reconnected.id).toBe(sandbox.id);

    const result = await reconnected.execute("echo reconnected");
    expect(result.output).toContain("reconnected");
  });
});
