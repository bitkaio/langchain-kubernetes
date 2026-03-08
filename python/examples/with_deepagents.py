"""Example: use KubernetesProvider as the sandbox backend for a DeepAgent.

Prerequisites:
  - agent-sandbox controller and CRDs installed in your cluster
  - sandbox-router deployed
  - SandboxTemplate "python-sandbox-template" applied:
      kubectl apply -f examples/k8s/sandbox-template.yaml

Run:
  python examples/with_deepagents.py
"""

from __future__ import annotations

from langchain_kubernetes import KubernetesProvider, KubernetesProviderConfig

# 1. Configure the provider — only connection and template params, no Pod config.
#    Pod-level settings (image, resources, runtime class) live in the SandboxTemplate.
config = KubernetesProviderConfig(
    template_name="python-sandbox-template",
    namespace="default",
    connection_mode="tunnel",
    startup_timeout_seconds=120,
    default_exec_timeout=300,  # 5 minutes per command
)
provider = KubernetesProvider(config)

# 2. Create a sandbox (or reconnect to an existing one by passing sandbox_id=...)
sandbox = provider.get_or_create()
print(f"Using sandbox: {sandbox.id}")

try:
    # 3. The sandbox implements SandboxBackendProtocol and can be passed
    #    directly to a DeepAgent as its backend:
    #
    #    from deepagents import create_agent
    #    from langchain_anthropic import ChatAnthropic
    #
    #    llm = ChatAnthropic(model="claude-opus-4-5")
    #    agent = create_agent(llm, backend=sandbox)
    #    result = agent.invoke({"messages": [("user", "Write a hello-world Python script")]})
    #    print(result)

    # 4. Or use it directly for isolated code execution
    result = sandbox.execute(
        "pip install requests --quiet && "
        "python3 -c 'import requests; print(requests.__version__)'"
    )
    print(f"requests version: {result.output.strip()}")

finally:
    provider.delete(sandbox_id=sandbox.id)
    print("Sandbox cleaned up.")
