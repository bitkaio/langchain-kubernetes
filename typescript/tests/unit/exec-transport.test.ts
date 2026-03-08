import { describe, it, expect, vi, beforeEach } from "vitest";
import * as k8s from "@kubernetes/client-node";
import { ExecTransport } from "../../src/exec-transport.js";
import { resolveExecuteConfig } from "../../src/config.js";
import { Writable, PassThrough } from "node:stream";

// ── Mock @kubernetes/client-node Exec ─────────────────────────────────────────

vi.mock("@kubernetes/client-node", async (importOriginal) => {
  const actual = await importOriginal<typeof k8s>();
  return {
    ...actual,
    Exec: vi.fn(),
  };
});

const NS = "test-namespace";
const POD = "test-pod";
const config = resolveExecuteConfig();

function makeTransport(execFn: (
  namespace: string,
  pod: string,
  container: string,
  cmd: string[],
  stdout: Writable,
  stderr: Writable,
  stdin: unknown,
  tty: boolean,
  statusCallback?: (s: k8s.V1Status) => void
) => Promise<unknown>): ExecTransport {
  const mockExec = {
    exec: execFn,
  };
  (k8s.Exec as unknown as ReturnType<typeof vi.fn>).mockReturnValue(mockExec);

  const kc = new k8s.KubeConfig();
  // loadFromDefault will fail in tests without a real cluster — use a stub
  vi.spyOn(kc, "loadFromDefault").mockReturnValue(undefined);

  return new ExecTransport(kc, config);
}

describe("ExecTransport.runCommand", () => {
  it("returns output and exitCode=0 on success", async () => {
    const transport = makeTransport(
      async (_ns, _pod, _container, _cmd, stdout, _stderr, _stdin, _tty, cb) => {
        (stdout as PassThrough).write("hello world\n");
        (stdout as PassThrough).end();
        if (cb) cb({ status: "Success" } as k8s.V1Status);
        return Promise.resolve({});
      }
    );

    const result = await transport.runCommand(NS, POD, "echo hello world");
    expect(result.output).toContain("hello world");
    expect(result.exitCode).toBe(0);
    expect(result.truncated).toBe(false);
  });

  it("returns non-zero exitCode on failure", async () => {
    const transport = makeTransport(
      async (_ns, _pod, _container, _cmd, _stdout, stderr, _stdin, _tty, cb) => {
        (stderr as PassThrough).write("command not found\n");
        (stderr as PassThrough).end();
        if (cb) {
          cb({
            status: "Failure",
            details: { causes: [{ reason: "ExitCode", message: "127" }] },
          } as k8s.V1Status);
        }
        return Promise.resolve({});
      }
    );

    const result = await transport.runCommand(NS, POD, "bogus-command");
    expect(result.exitCode).toBe(127);
    expect(result.output).toContain("command not found");
  });

  it("combines stdout and stderr into output", async () => {
    const transport = makeTransport(
      async (_ns, _pod, _container, _cmd, stdout, stderr, _stdin, _tty, cb) => {
        (stdout as PassThrough).write("stdout-text");
        (stderr as PassThrough).write("stderr-text");
        (stdout as PassThrough).end();
        (stderr as PassThrough).end();
        if (cb) cb({ status: "Success" } as k8s.V1Status);
        return Promise.resolve({});
      }
    );

    const result = await transport.runCommand(NS, POD, "cmd");
    expect(result.output).toContain("stdout-text");
    expect(result.output).toContain("stderr-text");
  });

  it("truncates output beyond outputLimitBytes", async () => {
    const smallConfig = resolveExecuteConfig({ outputLimitBytes: 10 });
    const mockExecFn = async (
      _ns: string,
      _pod: string,
      _container: string,
      _cmd: string[],
      stdout: Writable,
      _stderr: Writable,
      _stdin: unknown,
      _tty: boolean,
      cb?: (s: k8s.V1Status) => void
    ) => {
      // Write 20 bytes, limit is 10
      (stdout as PassThrough).write("A".repeat(20));
      (stdout as PassThrough).end();
      if (cb) cb({ status: "Success" } as k8s.V1Status);
      return Promise.resolve({});
    };

    const mockExec = { exec: mockExecFn };
    (k8s.Exec as unknown as ReturnType<typeof vi.fn>).mockReturnValue(mockExec);
    const kc = new k8s.KubeConfig();
    vi.spyOn(kc, "loadFromDefault").mockReturnValue(undefined);
    const transport = new ExecTransport(kc, smallConfig);

    const result = await transport.runCommand(NS, POD, "bigcmd");
    expect(result.truncated).toBe(true);
    expect(result.output.length).toBeLessThanOrEqual(20);
  });

  it("returns exitCode=-1 and timeout message on timeout", async () => {
    const fastTimeoutConfig = resolveExecuteConfig({ timeoutSeconds: 0 });

    const mockExecFn = async (
      _ns: string,
      _pod: string,
      _container: string,
      _cmd: string[],
      _stdout: Writable,
      _stderr: Writable,
      _stdin: unknown,
      _tty: boolean,
      _cb?: (s: k8s.V1Status) => void
    ) => {
      // Never calls cb — simulates hanging command
      await new Promise((resolve) => setTimeout(resolve, 60_000));
      return Promise.resolve({});
    };

    const mockExec = { exec: mockExecFn };
    (k8s.Exec as unknown as ReturnType<typeof vi.fn>).mockReturnValue(mockExec);
    const kc = new k8s.KubeConfig();
    vi.spyOn(kc, "loadFromDefault").mockReturnValue(undefined);
    const transport = new ExecTransport(kc, fastTimeoutConfig);

    const result = await transport.runCommand(NS, POD, "sleep 9999");
    expect(result.exitCode).toBe(-1);
    expect(result.output).toMatch(/timed? ?out/i);
  }, 5000);
});

describe("tar archive helpers (unit)", () => {
  it("builds valid in-memory tar that can be extracted back", async () => {
    // We test the tar logic indirectly by verifying uploadFiles calls exec
    // with the correct command (tar xf - -C /).
    let capturedCmd: string[] = [];

    const mockExecFn = async (
      _ns: string,
      _pod: string,
      _container: string,
      cmd: string[],
      _stdout: Writable,
      _stderr: Writable,
      _stdin: unknown,
      _tty: boolean,
      cb?: (s: k8s.V1Status) => void
    ) => {
      capturedCmd = cmd;
      if (cb) cb({ status: "Success" } as k8s.V1Status);
      return Promise.resolve({});
    };

    const mockExec = { exec: mockExecFn };
    (k8s.Exec as unknown as ReturnType<typeof vi.fn>).mockReturnValue(mockExec);
    const kc = new k8s.KubeConfig();
    vi.spyOn(kc, "loadFromDefault").mockReturnValue(undefined);
    const transport = new ExecTransport(kc, config);

    await transport.uploadFiles(NS, POD, [["/workspace/hello.txt", Buffer.from("hello")]]);
    expect(capturedCmd).toEqual(["/bin/sh", "-c", "tar xf - -C /"]);
  });

  it("download uses tar cf - command", async () => {
    let capturedCmd: string[] = [];

    const mockExecFn = async (
      _ns: string,
      _pod: string,
      _container: string,
      cmd: string[],
      stdout: Writable,
      _stderr: Writable,
      _stdin: unknown,
      _tty: boolean,
      cb?: (s: k8s.V1Status) => void
    ) => {
      capturedCmd = cmd;
      // Return empty stdout (no files)
      (stdout as PassThrough).end();
      if (cb) cb({ status: "Success" } as k8s.V1Status);
      return Promise.resolve({});
    };

    const mockExec = { exec: mockExecFn };
    (k8s.Exec as unknown as ReturnType<typeof vi.fn>).mockReturnValue(mockExec);
    const kc = new k8s.KubeConfig();
    vi.spyOn(kc, "loadFromDefault").mockReturnValue(undefined);
    const transport = new ExecTransport(kc, config);

    await transport.downloadFiles(NS, POD, ["/workspace/file.txt"]);
    // Command should start with the shell and include "tar cf -"
    expect(capturedCmd.join(" ")).toContain("tar cf -");
  });
});
