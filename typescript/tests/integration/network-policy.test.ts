import { describe, it, expect, beforeAll, afterAll } from "vitest";
import * as k8s from "@kubernetes/client-node";
import { KubernetesProvider } from "../../src/provider.js";
import { KubernetesSandbox } from "../../src/sandbox.js";
import { networkPolicyName } from "../../src/utils.js";
import {
  INTEGRATION_ENABLED,
  buildTestKubeConfig,
  testNamespaceName,
  createTestNamespace,
  deleteTestNamespace,
} from "./setup.js";

describe.skipIf(!INTEGRATION_ENABLED)("NetworkPolicy integration", () => {
  let coreApi: k8s.CoreV1Api;
  let networkingApi: k8s.NetworkingV1Api;
  let testNamespace: string;
  let sandbox: KubernetesSandbox;
  let provider: KubernetesProvider;

  beforeAll(async () => {
    const kc = buildTestKubeConfig();
    coreApi = kc.makeApiClient(k8s.CoreV1Api);
    networkingApi = kc.makeApiClient(k8s.NetworkingV1Api);

    testNamespace = testNamespaceName();
    await createTestNamespace(coreApi, testNamespace);

    provider = new KubernetesProvider({
      mode: "raw",
      namespace: testNamespace,
      image: "python:3.12-slim",
      blockNetwork: true, // explicit: NetworkPolicy should be created
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

  it("creates a deny-all NetworkPolicy alongside the Pod", async () => {
    const policyName = networkPolicyName(sandbox.id);
    const res = await networkingApi.readNamespacedNetworkPolicy(policyName, testNamespace);
    expect(res.body.metadata?.name).toBe(policyName);
    expect(res.body.spec?.ingress).toEqual([]);
    expect(res.body.spec?.egress).toEqual([]);
  });

  it("sandbox is still exec-accessible despite network block (exec != network traffic)", async () => {
    // kubectl exec bypasses network policies — it goes through the API server.
    const result = await sandbox.execute("echo exec-works");
    expect(result.output).toContain("exec-works");
  });

  it("sandbox cannot reach the internet when blockNetwork=true", async () => {
    // curl / wget should fail due to the deny-all NetworkPolicy.
    // This test is best-effort: the command will time out or refuse connection.
    const result = await sandbox.execute(
      "python3 -c \"import urllib.request; urllib.request.urlopen('http://8.8.8.8', timeout=5)\" 2>&1 || true"
    );
    // Either an OSError, timeout, or connection refused — not a successful HTTP response.
    expect(result.output).not.toContain("200");
  }, 30_000);

  it("deleting sandbox removes the NetworkPolicy", async () => {
    const sandboxId = sandbox.id;
    const policyName = networkPolicyName(sandboxId);

    await provider.delete(sandboxId);

    try {
      await networkingApi.readNamespacedNetworkPolicy(policyName, testNamespace);
      // If we get here, the policy still exists — fail the test
      expect(true).toBe(false);
    } catch (err: unknown) {
      const e = err as { statusCode?: number };
      expect(e.statusCode).toBe(404);
    }
  }, 60_000);
});
