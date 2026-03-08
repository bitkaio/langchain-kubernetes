/**
 * Example: using KubernetesProvider with a DeepAgents agent.
 *
 * Prerequisites:
 *   - ANTHROPIC_API_KEY set in the environment
 *   - A running Kubernetes cluster accessible via the default kubeconfig
 *
 * Run:
 *   ANTHROPIC_API_KEY=sk-... npx tsx examples/with-deepagents.ts
 */
import { createDeepAgent } from "deepagents";
import { ChatAnthropic } from "@langchain/anthropic";
import { KubernetesProvider } from "../src/index.js";

async function main() {
  const provider = new KubernetesProvider({
    image: "python:3.12-slim",
    blockNetwork: false,
    startupTimeoutSeconds: 120,
  });

  console.log("Provisioning Kubernetes sandbox…");
  const sandbox = await provider.getOrCreate();
  console.log(`Sandbox ready. id=${sandbox.id}`);

  const llm = new ChatAnthropic({
    model: "claude-haiku-4-5",
    temperature: 0,
  });

  const agent = createDeepAgent({
    llm,
    sandbox,
    systemPrompt: [
      "You are a helpful coding assistant with access to a Python 3.12 environment.",
      "You can run Python code, write files, and use shell commands.",
      "Always verify your work by running the code.",
    ].join("\n"),
  });

  console.log("\nRunning agent task…\n");

  const stream = await agent.stream({
    messages: [
      {
        role: "user",
        content: "Write a Python script that computes the first 10 Fibonacci numbers and save it to /workspace/fib.py. Then run it.",
      },
    ],
  });

  for await (const chunk of stream) {
    if ("agent" in chunk) {
      const msgs = (chunk as { agent: { messages: Array<{ content: unknown }> } }).agent.messages;
      for (const msg of msgs) {
        console.log("Agent:", msg.content);
      }
    }
  }

  // ── Retrieve the generated file ───────────────────────────────────────────
  const files = await sandbox.downloadFiles(["/workspace/fib.py"]);
  if (files[0]?.success) {
    console.log("\n/workspace/fib.py contents:\n");
    console.log(Buffer.from(files[0].content).toString());
  }

  // ── Cleanup ───────────────────────────────────────────────────────────────
  console.log("Deleting sandbox…");
  await provider.delete(sandbox.id);
  console.log("Done.");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
