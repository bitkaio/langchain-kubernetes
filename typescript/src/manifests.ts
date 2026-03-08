import * as k8s from "@kubernetes/client-node";
import type { KubernetesProviderConfig } from "./config.js";
import { sandboxLabels, networkPolicyName } from "./utils.js";

/**
 * Build a Pod manifest for a new sandbox.
 *
 * @param podName    - The Pod name (also used as part of the sandbox ID).
 * @param namespace  - The namespace the Pod will live in.
 * @param sandboxId  - The logical sandbox ID (used for labels).
 * @param config     - Provider configuration controlling image, resources, etc.
 */
export function buildPodManifest(
  podName: string,
  namespace: string,
  sandboxId: string,
  config: KubernetesProviderConfig
): k8s.V1Pod {
  const labels = sandboxLabels(sandboxId);

  const envVars: k8s.V1EnvVar[] = config.env
    ? Object.entries(config.env).map(([name, value]) => ({ name, value }))
    : [];

  const container: k8s.V1Container = {
    name: "sandbox",
    image: config.image,
    imagePullPolicy: config.imagePullPolicy,
    command: config.command,
    workingDir: config.workdir,
    env: envVars.length > 0 ? envVars : undefined,
    resources: {
      requests: {
        cpu: config.cpuRequest,
        memory: config.memoryRequest,
      },
      limits: {
        cpu: config.cpuLimit,
        memory: config.memoryLimit,
        "ephemeral-storage": config.ephemeralStorageLimit,
      },
    },
    securityContext: {
      runAsNonRoot: true,
      runAsUser: config.runAsUser,
      runAsGroup: config.runAsGroup,
      allowPrivilegeEscalation: false,
      capabilities: {
        drop: ["ALL"],
      },
      seccompProfile: {
        type: config.seccompProfile as k8s.V1SeccompProfile["type"],
      },
    },
    volumeMounts: config.volumeMounts,
  };

  const imagePullSecrets: k8s.V1LocalObjectReference[] | undefined =
    config.imagePullSecrets?.map((name) => ({ name }));

  const podSpec: k8s.V1PodSpec = {
    containers: [container],
    restartPolicy: "Never",
    automountServiceAccountToken: false,
    serviceAccountName: config.serviceAccount,
    imagePullSecrets,
    nodeSelector: config.nodeSelector,
    tolerations: config.tolerations,
    volumes: config.volumes,
    initContainers: config.initContainers,
    ...config.podTemplateOverrides,
  };

  return {
    apiVersion: "v1",
    kind: "Pod",
    metadata: {
      name: podName,
      namespace,
      labels,
      annotations: config.podTtlSeconds
        ? { "deepagents.langchain.com/ttl-seconds": String(config.podTtlSeconds) }
        : undefined,
    },
    spec: podSpec,
  };
}

/**
 * Build a Namespace manifest for the per-sandbox namespace strategy.
 *
 * @param namespaceName - The namespace name to create.
 * @param sandboxId     - The logical sandbox ID (used for labels).
 * @param config        - Provider configuration.
 */
export function buildNamespaceManifest(
  namespaceName: string,
  sandboxId: string,
  config: KubernetesProviderConfig
): k8s.V1Namespace {
  const labels: Record<string, string> = {
    ...sandboxLabels(sandboxId),
    ...config.namespaceLabels,
  };

  return {
    apiVersion: "v1",
    kind: "Namespace",
    metadata: {
      name: namespaceName,
      labels,
    },
  };
}

/**
 * Build a deny-all NetworkPolicy manifest that blocks all ingress and egress
 * for the sandbox Pod identified by `sandboxId`.
 *
 * @param namespace - The namespace the policy lives in.
 * @param sandboxId - The logical sandbox ID, used in label selectors and the policy name.
 */
export function buildNetworkPolicyManifest(
  namespace: string,
  sandboxId: string
): k8s.V1NetworkPolicy {
  return {
    apiVersion: "networking.k8s.io/v1",
    kind: "NetworkPolicy",
    metadata: {
      name: networkPolicyName(sandboxId),
      namespace,
      labels: sandboxLabels(sandboxId),
    },
    spec: {
      podSelector: {
        matchLabels: {
          "deepagents.langchain.com/sandbox-id": sandboxId,
        },
      },
      policyTypes: ["Ingress", "Egress"],
      ingress: [],
      egress: [],
    },
  };
}

/**
 * Build a ResourceQuota manifest that caps aggregate resource usage within
 * a namespace (used for the per-sandbox namespace strategy).
 *
 * @param namespace - The namespace the quota applies to.
 * @param sandboxId - The logical sandbox ID, used for labels.
 * @param config    - Provider configuration for resource limits.
 */
export function buildResourceQuotaManifest(
  namespace: string,
  sandboxId: string,
  config: KubernetesProviderConfig
): k8s.V1ResourceQuota {
  return {
    apiVersion: "v1",
    kind: "ResourceQuota",
    metadata: {
      name: "deepagents-sandbox-quota",
      namespace,
      labels: sandboxLabels(sandboxId),
    },
    spec: {
      hard: {
        pods: "1",
        "requests.cpu": config.cpuRequest,
        "requests.memory": config.memoryRequest,
        "limits.cpu": config.cpuLimit,
        "limits.memory": config.memoryLimit,
        "requests.ephemeral-storage": config.ephemeralStorageLimit,
      },
    },
  };
}
