import { describe, it, expect } from "vitest";
import {
  resolveConfig,
  resolveExecuteConfig,
  defaultConfig,
  defaultExecuteConfig,
} from "../../src/config.js";

describe("resolveConfig", () => {
  it("returns all defaults when called with no args", () => {
    const cfg = resolveConfig();
    expect(cfg.namespace).toBe("deepagents-sandboxes");
    expect(cfg.namespacePerSandbox).toBe(false);
    expect(cfg.image).toBe("python:3.12-slim");
    expect(cfg.imagePullPolicy).toBe("IfNotPresent");
    expect(cfg.workdir).toBe("/workspace");
    expect(cfg.command).toEqual(["sleep", "infinity"]);
    expect(cfg.cpuRequest).toBe("100m");
    expect(cfg.cpuLimit).toBe("1000m");
    expect(cfg.memoryRequest).toBe("256Mi");
    expect(cfg.memoryLimit).toBe("1Gi");
    expect(cfg.ephemeralStorageLimit).toBe("5Gi");
    expect(cfg.blockNetwork).toBe(true);
    expect(cfg.runAsUser).toBe(1000);
    expect(cfg.runAsGroup).toBe(1000);
    expect(cfg.seccompProfile).toBe("RuntimeDefault");
    expect(cfg.startupTimeoutSeconds).toBe(120);
    expect(cfg.podTtlSeconds).toBe(3600);
  });

  it("overrides specific fields while keeping defaults for the rest", () => {
    const cfg = resolveConfig({
      image: "node:20-slim",
      cpuLimit: "2000m",
      blockNetwork: false,
    });
    expect(cfg.image).toBe("node:20-slim");
    expect(cfg.cpuLimit).toBe("2000m");
    expect(cfg.blockNetwork).toBe(false);
    // Unchanged defaults
    expect(cfg.namespace).toBe("deepagents-sandboxes");
    expect(cfg.memoryLimit).toBe("1Gi");
  });

  it("does not mutate defaultConfig", () => {
    resolveConfig({ namespace: "custom-ns" });
    expect(defaultConfig.namespace).toBe("deepagents-sandboxes");
  });

  it("propagates undefined optional fields", () => {
    const cfg = resolveConfig();
    expect(cfg.kubeconfigPath).toBeUndefined();
    expect(cfg.context).toBeUndefined();
    expect(cfg.env).toBeUndefined();
    expect(cfg.serviceAccount).toBeUndefined();
    expect(cfg.nodeSelector).toBeUndefined();
    expect(cfg.namespaceLabels).toBeUndefined();
  });

  it("accepts env map", () => {
    const cfg = resolveConfig({ env: { FOO: "bar", BAZ: "42" } });
    expect(cfg.env).toEqual({ FOO: "bar", BAZ: "42" });
  });
});

describe("resolveExecuteConfig", () => {
  it("returns all defaults when called with no args", () => {
    const cfg = resolveExecuteConfig();
    expect(cfg.container).toBe("sandbox");
    expect(cfg.timeoutSeconds).toBe(300);
    expect(cfg.outputLimitBytes).toBe(1_000_000);
    expect(cfg.shell).toBe("/bin/sh");
  });

  it("overrides specific fields", () => {
    const cfg = resolveExecuteConfig({ timeoutSeconds: 60, shell: "/bin/bash" });
    expect(cfg.timeoutSeconds).toBe(60);
    expect(cfg.shell).toBe("/bin/bash");
    // unchanged
    expect(cfg.container).toBe("sandbox");
    expect(cfg.outputLimitBytes).toBe(1_000_000);
  });

  it("does not mutate defaultExecuteConfig", () => {
    resolveExecuteConfig({ container: "worker" });
    expect(defaultExecuteConfig.container).toBe("sandbox");
  });
});
