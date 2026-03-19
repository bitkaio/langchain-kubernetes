# OpenShift Notes

`langchain-kubernetes` works on OpenShift with minor configuration adjustments.

## SecurityContextConstraints (SCC)

OpenShift's SCC admission controller rejects Pods that run as a specific UID without a matching SCC. The default raw-mode config uses `runAsUser: 1000` which may be rejected.

### Option 1: Use the `anyuid` SCC (easiest, less secure)

```bash
oc adm policy add-scc-to-serviceaccount anyuid -z langchain-kubernetes -n <namespace>
```

Then configure the provider:

```python
config = KubernetesProviderConfig(
    mode="raw",
    service_account="langchain-kubernetes",
    run_as_user=1000,
    run_as_group=1000,
)
```

### Option 2: Use the `restricted-v2` SCC (recommended)

Drop the `runAsUser` / `runAsGroup` from the provider config and let OpenShift assign a UID from the namespace's allowed range:

```python
config = KubernetesProviderConfig(
    mode="raw",
    run_as_user=None,    # let OpenShift assign
    run_as_group=None,
    seccomp_profile="RuntimeDefault",
)
```

```typescript
const provider = new KubernetesProvider({
  mode: "raw",
  runAsUser: undefined,  // omit → OpenShift assigns from namespace range
  runAsGroup: undefined,
});
```

OpenShift assigns a UID from the namespace's `openshift.io/sa.scc.uid-range` annotation. All write paths inside the container must be writable by that UID (the default `python:3.12-slim` image satisfies this).

### Option 3: Custom SCC

For production, create a dedicated SCC that grants exactly the permissions needed:

```yaml
apiVersion: security.openshift.io/v1
kind: SecurityContextConstraints
metadata:
  name: langchain-kubernetes-sandbox
allowPrivilegeEscalation: false
allowPrivilegedContainer: false
defaultAddCapabilities: []
requiredDropCapabilities: [ALL]
fsGroup:
  type: MustRunAs
  ranges:
    - min: 1000
      max: 1000
runAsUser:
  type: MustRunAsRange
  uidRangeMin: 1000
  uidRangeMax: 1000
seLinuxContext:
  type: MustRunAs
seccompProfiles:
  - runtime/default
volumes: [configMap, emptyDir, secret]
users: []
groups: []
```

## NetworkPolicy on OpenShift

OpenShift uses `NetworkPolicy` in the same way as upstream Kubernetes. The default `blockNetwork: true` setting creates a deny-all `NetworkPolicy` for each sandbox Pod, which is supported on OpenShift with the OVN-Kubernetes or OpenShift SDN CNI plugins.

If you use OpenShift's multitenant mode (deprecated), network isolation is provided at the namespace level and you can disable the provider's own NetworkPolicy:

```python
config = KubernetesProviderConfig(mode="raw", block_network=False)
```

## Routes vs Ingress

`langchain-kubernetes` does not create Ingress resources for sandboxes (all communication is internal). No Route configuration is needed.

If the sandbox-router service (agent-sandbox mode) is exposed via an OpenShift Route, use the Route hostname as `routerUrl`:

```python
config = KubernetesProviderConfig(
    mode="agent-sandbox",
    template_name="python-sandbox-template",
    api_url="https://sandbox-router.apps.your-cluster.example.com",
)
```

## Operator Hub / OLM

The `kubernetes-sigs/agent-sandbox` controller can be installed via OLM if a bundle is available in your catalog. Check the project's releases for an OLM bundle before attempting a manual `kubectl apply`.

For manual installation on OpenShift:

```bash
oc apply -f https://github.com/kubernetes-sigs/agent-sandbox/releases/latest/download/install.yaml
```

If CRD installation is rejected by admission (e.g., OpenShift requires CRDs to be cluster-scoped), contact your cluster administrator to apply the CRDs with cluster-admin privileges.

## Quotas and LimitRanges

OpenShift namespaces often have `LimitRange` objects that impose default/max resource limits. Ensure the provider's resource settings are within the namespace's limits:

```python
config = KubernetesProviderConfig(
    mode="raw",
    cpu_request="100m",
    cpu_limit="500m",          # must be within LimitRange max
    memory_request="128Mi",
    memory_limit="512Mi",      # must be within LimitRange max
    ephemeral_storage_limit="2Gi",
)
```
