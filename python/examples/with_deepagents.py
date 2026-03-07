"""Example: use KubernetesProvider as the sandbox backend for a DeepAgent."""

from __future__ import annotations

from langchain_kubernetes import KubernetesProvider, KubernetesProviderConfig

# 1. Configure and create the provider
config = KubernetesProviderConfig(
    namespace="deepagents-sandboxes",
    image="python:3.12-slim",
    block_network=True,
    cpu_limit="2",
    memory_limit="512Mi",
    startup_timeout=120,
    default_exec_timeout=300,  # 5 minutes per command
)
provider = KubernetesProvider(config=config)

# 2. Create a sandbox (or reconnect to an existing one)
sandbox = provider.get_or_create()
print(f"Using sandbox: {sandbox.id}")

try:
    # 3. The sandbox implements SandboxBackendProtocol, so it can be passed
    #    directly to a DeepAgent as its backend.
    #
    #    Example (requires `deepagents` and a LLM):
    #
    #    from deepagents import create_agent
    #    from langchain_anthropic import ChatAnthropic
    #
    #    llm = ChatAnthropic(model="claude-3-5-sonnet-latest")
    #    agent = create_agent(llm, backend=sandbox)
    #    result = agent.invoke({"messages": [("user", "Write a hello-world Python script")]})
    #    print(result)

    # 4. Or use it directly for isolated code execution
    result = sandbox.execute("pip install requests --quiet && python3 -c 'import requests; print(requests.__version__)'")
    print(f"requests version: {result.output.strip()}")

finally:
    provider.delete(sandbox_id=sandbox.id)
    print("Sandbox cleaned up.")
