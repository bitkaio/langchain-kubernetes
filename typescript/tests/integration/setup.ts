/**
 * Integration test helpers for setting up/tearing down test namespaces
 * in a kind (or any accessible) Kubernetes cluster.
 *
 * Integration tests are skipped unless K8S_INTEGRATION=1 is set.
 */
import * as k8s from "@kubernetes/client-node";
import { generateSuffix } from "../../src/utils.js";

export const INTEGRATION_ENABLED = !!process.env["K8S_INTEGRATION"];

/** Build a KubeConfig from the environment (default kubeconfig or in-cluster). */
export function buildTestKubeConfig(): k8s.KubeConfig {
  const kc = new k8s.KubeConfig();
  kc.loadFromDefault();
  return kc;
}

/** Generate a unique test namespace name. */
export function testNamespaceName(): string {
  return `deepagents-test-${generateSuffix()}`;
}

/** Create a namespace for a test run. */
export async function createTestNamespace(
  coreApi: k8s.CoreV1Api,
  namespaceName: string
): Promise<void> {
  await coreApi.createNamespace({
    apiVersion: "v1",
    kind: "Namespace",
    metadata: {
      name: namespaceName,
      labels: {
        "app.kubernetes.io/managed-by": "deepagents-test",
        "deepagents.langchain.com/test": "true",
      },
    },
  });
}

/**
 * Delete a namespace and all resources within it.
 * Ignores 404 (already deleted).
 */
export async function deleteTestNamespace(
  coreApi: k8s.CoreV1Api,
  namespaceName: string
): Promise<void> {
  try {
    await coreApi.deleteNamespace(namespaceName);
  } catch (err: unknown) {
    const e = err as { statusCode?: number };
    if (e.statusCode !== 404) throw err;
  }
}

/**
 * Wait until a namespace is fully terminated (no longer exists).
 * Useful after deleteTestNamespace to avoid resource conflicts in subsequent tests.
 */
export async function waitForNamespaceDeletion(
  coreApi: k8s.CoreV1Api,
  namespaceName: string,
  timeoutMs = 60_000
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      await coreApi.readNamespace(namespaceName);
      await new Promise((r) => setTimeout(r, 2000));
    } catch (err: unknown) {
      const e = err as { statusCode?: number };
      if (e.statusCode === 404) return;
      throw err;
    }
  }
  throw new Error(`Namespace ${namespaceName} was not deleted within ${timeoutMs}ms`);
}

/** Wait for a Pod to reach Running phase, polling every 2s. */
export async function waitForPodRunning(
  coreApi: k8s.CoreV1Api,
  namespace: string,
  podName: string,
  timeoutMs = 120_000
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const res = await coreApi.readNamespacedPod(podName, namespace);
    if (res.body.status?.phase === "Running") return;
    await new Promise((r) => setTimeout(r, 2000));
  }
  throw new Error(`Pod ${podName} did not reach Running within ${timeoutMs}ms`);
}
