"""Basic usage: create a sandbox, run commands, transfer files, clean up.

Prerequisites:
  - agent-sandbox controller and CRDs installed in your cluster
  - sandbox-router deployed
  - SandboxTemplate "python-sandbox-template" applied:
      kubectl apply -f examples/k8s/sandbox-template.yaml

Run:
  python examples/basic_usage.py
"""

from __future__ import annotations

from langchain_kubernetes import KubernetesProvider, KubernetesProviderConfig

config = KubernetesProviderConfig(
    template_name="python-sandbox-template",
    namespace="default",
    connection_mode="tunnel",  # uses kubectl port-forward automatically
    startup_timeout_seconds=120,
)

provider = KubernetesProvider(config)

# Create a new sandbox (provisions a Sandbox CR, waits for readiness)
sandbox = provider.get_or_create()
print(f"Sandbox created: {sandbox.id}")

try:
    # Run a shell command
    result = sandbox.execute("echo 'Hello from Kubernetes!'")
    print(f"Output: {result.output!r}  exit_code={result.exit_code}")

    # Run a Python snippet
    result = sandbox.execute("python3 -c 'import sys; print(sys.version)'")
    print(f"Python version: {result.output.strip()}")

    # Upload a file
    upload_resp = sandbox.upload_files([("/tmp/demo.txt", b"Hello, DeepAgents!\n")])
    print(f"Upload: {upload_resp[0].path!r}  error={upload_resp[0].error}")

    # Download the file back
    dl_resp = sandbox.download_files(["/tmp/demo.txt"])
    print(f"Downloaded {len(dl_resp[0].content or b'')} bytes from /tmp/demo.txt")

    # Use BaseSandbox filesystem helpers (implemented via execute())
    sandbox.execute("mkdir -p /tmp/project && echo 'x=1' > /tmp/project/script.py")
    entries = sandbox.ls_info("/tmp/project")
    print(f"ls /tmp/project: {[e['path'] for e in entries]}")

    # List active sandboxes managed by this provider instance
    active = provider.list()
    print(f"Active sandboxes: {[s.id for s in active]}")

finally:
    provider.delete(sandbox_id=sandbox.id)
    print("Sandbox deleted.")
