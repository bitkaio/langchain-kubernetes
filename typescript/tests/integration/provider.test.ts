import { describe, it, expect, beforeAll, afterAll } from "vitest";
import * as k8s from "@kubernetes/client-node";
import { KubernetesProvider } from "../../src/provider.js";
import { KubernetesSandbox } from "../../src/sandbox.js";
import { SandboxNotFoundError, SandboxStartupTimeoutError } from "../../src/errors.js";
import {
  INTEGRATION_ENABLED,
  buildTestKubeConfig,
  testNamespaceName,
  createTestNamespace,
  deleteTestNamespace,
} from "./setup.js";

describe.skipIf(!INTEGRATION_ENABLED)("KubernetesProvider integration", () => {
  let coreApi: k8s.CoreV1Api;
  let testNamespace: string;
  const createdSandboxIds: string[] = [];

  beforeAll(async () => {
    const kc = buildTestKubeConfig();
    coreApi = kc.makeApiClient(k8s.CoreV1Api);
    testNamespace = testNamespaceName();
    await createTestNamespace(coreApi, testNamespace);
  }, 60_000);

  afterAll(async () => {
    await deleteTestNamespace(coreApi, testNamespace);
  }, 120_000);

  function makeProvider(overrides?: Record<string, unknown>): KubernetesProvider {
    return new KubernetesProvider({
      mode: "raw",
      namespace: testNamespace,
      image: "python:3.12-slim",
      blockNetwork: false,
      startupTimeoutSeconds: 120,
      podTtlSeconds: undefined,
      ...overrides,
    });
  }

  it("getOrCreate creates a new sandbox", async () => {
    const provider = makeProvider();
    const sandbox = await provider.getOrCreate();
    createdSandboxIds.push(sandbox.id);

    expect(sandbox).toBeInstanceOf(KubernetesSandbox);
    expect(sandbox.id).toBeTruthy();

    await provider.delete(sandbox.id);
  }, 180_000);

  it("list returns created sandboxes", async () => {
    const provider = makeProvider();
    const sandbox = await provider.getOrCreate();
    createdSandboxIds.push(sandbox.id);

    const { sandboxes } = await provider.list();
    const ids = sandboxes.map((s) => s.id);
    expect(ids).toContain(sandbox.id);

    await provider.delete(sandbox.id);
  }, 180_000);

  it("delete removes the Pod", async () => {
    const provider = makeProvider();
    const sandbox = await provider.getOrCreate();

    await provider.delete(sandbox.id);

    // Pod should no longer be Running
    const { sandboxes } = await provider.list();
    const ids = sandboxes.map((s) => s.id);
    expect(ids).not.toContain(sandbox.id);
  }, 180_000);

  it("delete is idempotent — deleting a non-existent sandbox is a no-op", async () => {
    const provider = makeProvider();
    await expect(provider.delete("deepagents-sandbox-does-not-exist")).resolves.toBeUndefined();
  });

  it("getOrCreate(existingId) reconnects to a running sandbox", async () => {
    const provider = makeProvider();
    const sandbox = await provider.getOrCreate();
    createdSandboxIds.push(sandbox.id);

    const reconnected = await provider.getOrCreate(sandbox.id);
    expect(reconnected.id).toBe(sandbox.id);

    await provider.delete(sandbox.id);
  }, 180_000);

  it("getOrCreate(deletedId) throws SandboxNotFoundError", async () => {
    const provider = makeProvider();
    await expect(provider.getOrCreate("deepagents-sandbox-gone")).rejects.toThrow(
      SandboxNotFoundError
    );
  });

  it("throws SandboxStartupTimeoutError when timeout is too short", async () => {
    // Use an image that does not exist to force a slow / never-running pod,
    // combined with an unrealistically short timeout.
    const provider = makeProvider({
      image: "this-image-does-not-exist-at-all:latest",
      imagePullPolicy: "Always",
      startupTimeoutSeconds: 5,
    });

    await expect(provider.getOrCreate()).rejects.toThrow(SandboxStartupTimeoutError);
  }, 60_000);
});
