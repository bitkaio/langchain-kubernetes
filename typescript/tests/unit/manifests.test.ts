import { describe, it, expect } from "vitest";
import {
  buildPodManifest,
  buildNamespaceManifest,
  buildNetworkPolicyManifest,
  buildResourceQuotaManifest,
} from "../../src/manifests.js";
import { resolveConfig } from "../../src/config.js";
import {
  LABEL_MANAGED_BY,
  LABEL_COMPONENT,
  LABEL_SANDBOX_ID,
  MANAGED_BY_VALUE,
  COMPONENT_VALUE,
} from "../../src/utils.js";

const cfg = resolveConfig();
const SANDBOX_ID = "deepagents-sandbox-a1b2c3d4";
const NS = "deepagents-sandboxes";
const POD = "deepagents-sandbox-a1b2c3d4";

describe("buildPodManifest", () => {
  it("sets apiVersion, kind, name, namespace", () => {
    const pod = buildPodManifest(POD, NS, SANDBOX_ID, cfg);
    expect(pod.apiVersion).toBe("v1");
    expect(pod.kind).toBe("Pod");
    expect(pod.metadata?.name).toBe(POD);
    expect(pod.metadata?.namespace).toBe(NS);
  });

  it("applies all required labels", () => {
    const pod = buildPodManifest(POD, NS, SANDBOX_ID, cfg);
    const labels = pod.metadata?.labels ?? {};
    expect(labels[LABEL_MANAGED_BY]).toBe(MANAGED_BY_VALUE);
    expect(labels[LABEL_COMPONENT]).toBe(COMPONENT_VALUE);
    expect(labels[LABEL_SANDBOX_ID]).toBe(SANDBOX_ID);
  });

  it("sets restartPolicy to Never", () => {
    const pod = buildPodManifest(POD, NS, SANDBOX_ID, cfg);
    expect(pod.spec?.restartPolicy).toBe("Never");
  });

  it("disables service account token automount", () => {
    const pod = buildPodManifest(POD, NS, SANDBOX_ID, cfg);
    expect(pod.spec?.automountServiceAccountToken).toBe(false);
  });

  it("applies security context to the container", () => {
    const pod = buildPodManifest(POD, NS, SANDBOX_ID, cfg);
    const sc = pod.spec?.containers[0].securityContext;
    expect(sc?.runAsNonRoot).toBe(true);
    expect(sc?.runAsUser).toBe(1000);
    expect(sc?.runAsGroup).toBe(1000);
    expect(sc?.allowPrivilegeEscalation).toBe(false);
    expect(sc?.capabilities?.drop).toContain("ALL");
    expect(sc?.seccompProfile?.type).toBe("RuntimeDefault");
  });

  it("sets resource requests and limits", () => {
    const pod = buildPodManifest(POD, NS, SANDBOX_ID, cfg);
    const resources = pod.spec?.containers[0].resources;
    expect(resources?.requests?.["cpu"]).toBe("100m");
    expect(resources?.requests?.["memory"]).toBe("256Mi");
    expect(resources?.limits?.["cpu"]).toBe("1000m");
    expect(resources?.limits?.["memory"]).toBe("1Gi");
    expect(resources?.limits?.["ephemeral-storage"]).toBe("5Gi");
  });

  it("sets image and working directory", () => {
    const pod = buildPodManifest(POD, NS, SANDBOX_ID, cfg);
    const container = pod.spec?.containers[0];
    expect(container?.image).toBe("python:3.12-slim");
    expect(container?.workingDir).toBe("/workspace");
  });

  it("applies env vars when provided", () => {
    const cfgWithEnv = resolveConfig({ env: { MY_VAR: "hello", NUM: "42" } });
    const pod = buildPodManifest(POD, NS, SANDBOX_ID, cfgWithEnv);
    const env = pod.spec?.containers[0].env ?? [];
    expect(env).toContainEqual({ name: "MY_VAR", value: "hello" });
    expect(env).toContainEqual({ name: "NUM", value: "42" });
  });

  it("omits env field when no env vars", () => {
    const pod = buildPodManifest(POD, NS, SANDBOX_ID, cfg);
    expect(pod.spec?.containers[0].env).toBeUndefined();
  });

  it("sets TTL annotation when podTtlSeconds is set", () => {
    const pod = buildPodManifest(POD, NS, SANDBOX_ID, cfg);
    expect(pod.metadata?.annotations?.["deepagents.langchain.com/ttl-seconds"]).toBe("3600");
  });

  it("omits TTL annotation when podTtlSeconds is undefined", () => {
    const cfgNoTtl = resolveConfig({ podTtlSeconds: undefined });
    const pod = buildPodManifest(POD, NS, SANDBOX_ID, cfgNoTtl);
    expect(pod.metadata?.annotations).toBeUndefined();
  });

  it("applies custom runAsUser/runAsGroup", () => {
    const customCfg = resolveConfig({ runAsUser: 2000, runAsGroup: 2000 });
    const pod = buildPodManifest(POD, NS, SANDBOX_ID, customCfg);
    const sc = pod.spec?.containers[0].securityContext;
    expect(sc?.runAsUser).toBe(2000);
    expect(sc?.runAsGroup).toBe(2000);
  });

  it("attaches imagePullSecrets when specified", () => {
    const cfgSec = resolveConfig({ imagePullSecrets: ["my-secret"] });
    const pod = buildPodManifest(POD, NS, SANDBOX_ID, cfgSec);
    expect(pod.spec?.imagePullSecrets).toContainEqual({ name: "my-secret" });
  });
});

describe("buildNamespaceManifest", () => {
  it("sets apiVersion, kind, and name", () => {
    const ns = buildNamespaceManifest(NS, SANDBOX_ID, cfg);
    expect(ns.apiVersion).toBe("v1");
    expect(ns.kind).toBe("Namespace");
    expect(ns.metadata?.name).toBe(NS);
  });

  it("applies required labels", () => {
    const ns = buildNamespaceManifest(NS, SANDBOX_ID, cfg);
    const labels = ns.metadata?.labels ?? {};
    expect(labels[LABEL_MANAGED_BY]).toBe(MANAGED_BY_VALUE);
    expect(labels[LABEL_COMPONENT]).toBe(COMPONENT_VALUE);
    expect(labels[LABEL_SANDBOX_ID]).toBe(SANDBOX_ID);
  });

  it("merges namespaceLabels from config", () => {
    const cfgWithNsLabels = resolveConfig({
      namespaceLabels: { "team": "platform", "env": "test" },
    });
    const ns = buildNamespaceManifest(NS, SANDBOX_ID, cfgWithNsLabels);
    const labels = ns.metadata?.labels ?? {};
    expect(labels["team"]).toBe("platform");
    expect(labels["env"]).toBe("test");
    // Required labels still present
    expect(labels[LABEL_MANAGED_BY]).toBe(MANAGED_BY_VALUE);
  });
});

describe("buildNetworkPolicyManifest", () => {
  it("creates a deny-all policy", () => {
    const np = buildNetworkPolicyManifest(NS, SANDBOX_ID);
    expect(np.apiVersion).toBe("networking.k8s.io/v1");
    expect(np.kind).toBe("NetworkPolicy");
    expect(np.spec?.ingress).toEqual([]);
    expect(np.spec?.egress).toEqual([]);
    expect(np.spec?.policyTypes).toContain("Ingress");
    expect(np.spec?.policyTypes).toContain("Egress");
  });

  it("selects by sandbox ID label", () => {
    const np = buildNetworkPolicyManifest(NS, SANDBOX_ID);
    expect(np.spec?.podSelector?.matchLabels?.[LABEL_SANDBOX_ID]).toBe(SANDBOX_ID);
  });

  it("names the policy with sandbox ID suffix", () => {
    const np = buildNetworkPolicyManifest(NS, SANDBOX_ID);
    expect(np.metadata?.name).toContain(SANDBOX_ID);
    expect(np.metadata?.name).toMatch(/^deepagents-sandbox-deny-all-/);
  });

  it("handles sandboxId with slash (namespacePerSandbox)", () => {
    const id = "my-ns/my-pod";
    const np = buildNetworkPolicyManifest("my-ns", id);
    // Slashes replaced with dashes in policy name
    expect(np.metadata?.name).not.toContain("/");
  });
});

describe("buildResourceQuotaManifest", () => {
  it("restricts to 1 Pod", () => {
    const rq = buildResourceQuotaManifest(NS, SANDBOX_ID, cfg);
    expect(rq.spec?.hard?.["pods"]).toBe("1");
  });

  it("sets cpu and memory limits from config", () => {
    const rq = buildResourceQuotaManifest(NS, SANDBOX_ID, cfg);
    expect(rq.spec?.hard?.["limits.cpu"]).toBe("1000m");
    expect(rq.spec?.hard?.["limits.memory"]).toBe("1Gi");
    expect(rq.spec?.hard?.["requests.cpu"]).toBe("100m");
    expect(rq.spec?.hard?.["requests.memory"]).toBe("256Mi");
  });

  it("applies required labels", () => {
    const rq = buildResourceQuotaManifest(NS, SANDBOX_ID, cfg);
    expect(rq.metadata?.labels?.[LABEL_MANAGED_BY]).toBe(MANAGED_BY_VALUE);
  });
});
