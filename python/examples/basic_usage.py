"""Basic usage example: create a sandbox, run commands, clean up."""

from __future__ import annotations

from langchain_kubernetes import KubernetesProvider, KubernetesProviderConfig

config = KubernetesProviderConfig(
    namespace="deepagents-sandboxes",
    image="python:3.12-slim",
    block_network=True,
    startup_timeout=120,
)

provider = KubernetesProvider(config=config)

# Create a new ephemeral sandbox
sandbox = provider.get_or_create()
print(f"Sandbox created: {sandbox.id}")

try:
    # Run a shell command
    result = sandbox.execute("echo 'Hello from Kubernetes!'")
    print(f"Output: {result.output!r}  exit_code={result.exit_code}")

    # Run a Python snippet
    result = sandbox.execute("python3 -c 'import sys; print(sys.version)'")
    print(f"Python version: {result.output.strip()}")

    # Write and read a file using BaseSandbox helpers
    sandbox.write("/tmp/demo.txt", "Hello, DeepAgents!\n")
    content = sandbox.read("/tmp/demo.txt")
    print(f"File content: {content!r}")

    # Upload binary files
    upload_resp = sandbox.upload_files([("/tmp/data.bin", b"\x00\x01\x02\x03")])
    print(f"Upload: {upload_resp}")

    # Download files
    dl_resp = sandbox.download_files(["/tmp/demo.txt"])
    print(f"Downloaded {len(dl_resp[0].content)} bytes from /tmp/demo.txt")

    # List sandboxes
    all_sandboxes = provider.list()
    print(f"Active sandboxes: {[s.id for s in all_sandboxes]}")

finally:
    provider.delete(sandbox_id=sandbox.id)
    print("Sandbox deleted.")
