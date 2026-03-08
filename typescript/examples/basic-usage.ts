/**
 * Basic usage example for langchain-kubernetes.
 *
 * Prerequisites:
 *   - A running Kubernetes cluster accessible via the default kubeconfig
 *   - The "deepagents-sandboxes" namespace (or let the provider create it)
 *
 * Run:
 *   npx tsx examples/basic-usage.ts
 */
import { KubernetesProvider } from "../src/index.js";

async function main() {
  const provider = new KubernetesProvider({
    image: "python:3.12-slim",
    blockNetwork: false, // enable network for this demo
    startupTimeoutSeconds: 120,
  });

  console.log("Creating sandbox…");
  const sandbox = await provider.getOrCreate();
  console.log(`Sandbox ready. id=${sandbox.id}`);

  // ── Execute a simple command ──────────────────────────────────────────────
  let result = await sandbox.execute("python3 -c \"print('Hello from Kubernetes!')\"");
  console.log("stdout:", result.output.trim());
  console.log("exitCode:", result.exitCode);

  // ── Write and read a file via the inherited BaseSandbox helpers ───────────
  result = await sandbox.execute("echo 'Hello, file!' > /workspace/greeting.txt");
  result = await sandbox.execute("cat /workspace/greeting.txt");
  console.log("file content:", result.output.trim());

  // ── Upload a file via tar transport ───────────────────────────────────────
  const uploadResults = await sandbox.uploadFiles([
    ["/workspace/script.py", Buffer.from("print('Uploaded script ran!')\n")],
  ]);
  console.log("upload success:", uploadResults[0]?.success);

  result = await sandbox.execute("python3 /workspace/script.py");
  console.log("script output:", result.output.trim());

  // ── Download a file via tar transport ─────────────────────────────────────
  const downloadResults = await sandbox.downloadFiles(["/workspace/greeting.txt"]);
  console.log(
    "downloaded:",
    Buffer.from(downloadResults[0]!.content).toString().trim()
  );

  // ── Cleanup ───────────────────────────────────────────────────────────────
  console.log("Deleting sandbox…");
  await provider.delete(sandbox.id);
  console.log("Done.");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
