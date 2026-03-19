import { describe, it, expect } from "vitest";
import {
  sanitizeLabelValue,
  buildLabels,
  buildTtlAnnotations,
  threadIdSelector,
  warmPoolSelector,
  LABEL_PREFIX,
  LK_LABEL_MANAGED_BY,
  LK_MANAGED_BY_VALUE,
  LK_LABEL_THREAD_ID,
  ANN_THREAD_ID_ORIGINAL,
  ANN_TTL_SECONDS,
  ANN_TTL_IDLE_SECONDS,
  ANN_CREATED_AT,
} from "../../src/labels.js";

// ── sanitizeLabelValue ─────────────────────────────────────────────────────────

describe("sanitizeLabelValue", () => {
  it("passes through valid short alphanumeric values", () => {
    const [safe, original] = sanitizeLabelValue("my-thread");
    expect(safe).toBe("my-thread");
    expect(original).toBeUndefined();
  });

  it("passes through exactly-63-char values", () => {
    const value = "a".repeat(63);
    const [safe, original] = sanitizeLabelValue(value);
    expect(safe).toBe(value);
    expect(original).toBeUndefined();
  });

  it("hashes values longer than 63 chars", () => {
    const value = "a".repeat(64);
    const [safe, original] = sanitizeLabelValue(value);
    expect(safe).toHaveLength(12);
    expect(safe).toMatch(/^[0-9a-f]+$/);
    expect(original).toBe(value);
  });

  it("hashes values with invalid characters (spaces)", () => {
    const value = "my thread id";
    const [safe, original] = sanitizeLabelValue(value);
    expect(safe).toHaveLength(12);
    expect(safe).toMatch(/^[0-9a-f]+$/);
    expect(original).toBe(value);
  });

  it("hashes values with invalid characters (slashes)", () => {
    const value = "user/conv/123";
    const [safe, original] = sanitizeLabelValue(value);
    expect(safe).toHaveLength(12);
    expect(original).toBe(value);
  });

  it("passes through empty string", () => {
    const [safe, original] = sanitizeLabelValue("");
    expect(safe).toBe("");
    expect(original).toBeUndefined();
  });

  it("hashes values starting with a hyphen", () => {
    const [safe, original] = sanitizeLabelValue("-bad-start");
    expect(safe).toHaveLength(12);
    expect(original).toBe("-bad-start");
  });

  it("hashes values ending with a hyphen", () => {
    const [safe, original] = sanitizeLabelValue("bad-end-");
    expect(safe).toHaveLength(12);
    expect(original).toBe("bad-end-");
  });

  it("produces stable hashes for the same input", () => {
    const value = "unstable thread!@#";
    const [safe1] = sanitizeLabelValue(value);
    const [safe2] = sanitizeLabelValue(value);
    expect(safe1).toBe(safe2);
  });

  it("produces different hashes for different inputs", () => {
    const [safe1] = sanitizeLabelValue("thread-a-very-long-value-that-exceeds-limit-of-63-chars-here!!!");
    const [safe2] = sanitizeLabelValue("thread-b-very-long-value-that-exceeds-limit-of-63-chars-here!!!");
    expect(safe1).not.toBe(safe2);
  });
});

// ── buildLabels ────────────────────────────────────────────────────────────────

describe("buildLabels", () => {
  it("always includes managed-by label", () => {
    const [labels] = buildLabels({});
    expect(labels[LK_LABEL_MANAGED_BY]).toBe(LK_MANAGED_BY_VALUE);
  });

  it("prefixes defaultLabels keys", () => {
    const [labels] = buildLabels({ defaultLabels: { env: "test" } });
    expect(labels[`${LABEL_PREFIX}env`]).toBe("test");
  });

  it("prefixes callLabels keys and callLabels overrides defaultLabels", () => {
    const [labels] = buildLabels({
      defaultLabels: { env: "prod" },
      callLabels: { env: "staging" },
    });
    expect(labels[`${LABEL_PREFIX}env`]).toBe("staging");
  });

  it("adds thread_id label when threadId is provided", () => {
    const [labels] = buildLabels({ threadId: "my-thread" });
    expect(labels[LK_LABEL_THREAD_ID]).toBe("my-thread");
  });

  it("sanitizes thread_id and stores original as annotation", () => {
    const longId = "a".repeat(64);
    const [labels, annotations] = buildLabels({ threadId: longId });
    expect(labels[LK_LABEL_THREAD_ID]).toHaveLength(12);
    expect(annotations[ANN_THREAD_ID_ORIGINAL]).toBe(longId);
  });

  it("returns empty annotations when threadId is valid", () => {
    const [, annotations] = buildLabels({ threadId: "valid-thread" });
    expect(Object.keys(annotations)).toHaveLength(0);
  });

  it("callLabels win over defaultLabels (merged in order)", () => {
    const [labels] = buildLabels({
      defaultLabels: { project: "alpha", env: "prod" },
      callLabels: { env: "staging" },
    });
    expect(labels[`${LABEL_PREFIX}project`]).toBe("alpha");
    expect(labels[`${LABEL_PREFIX}env`]).toBe("staging");
  });
});

// ── buildTtlAnnotations ────────────────────────────────────────────────────────

describe("buildTtlAnnotations", () => {
  it("returns empty object when neither TTL is set", () => {
    const ann = buildTtlAnnotations({});
    expect(Object.keys(ann)).toHaveLength(0);
  });

  it("sets ttl-seconds and created-at when ttlSeconds is provided", () => {
    const ann = buildTtlAnnotations({ ttlSeconds: 3600 });
    expect(ann[ANN_TTL_SECONDS]).toBe("3600");
    expect(ann[ANN_CREATED_AT]).toBeDefined();
    // created-at should be a valid ISO date
    expect(new Date(ann[ANN_CREATED_AT]).getTime()).not.toBeNaN();
  });

  it("sets ttl-idle-seconds when ttlIdleSeconds is provided", () => {
    const ann = buildTtlAnnotations({ ttlIdleSeconds: 600 });
    expect(ann[ANN_TTL_IDLE_SECONDS]).toBe("600");
    expect(ann[ANN_CREATED_AT]).toBeUndefined();
  });

  it("sets both TTL annotations together", () => {
    const ann = buildTtlAnnotations({ ttlSeconds: 7200, ttlIdleSeconds: 1800 });
    expect(ann[ANN_TTL_SECONDS]).toBe("7200");
    expect(ann[ANN_TTL_IDLE_SECONDS]).toBe("1800");
    expect(ann[ANN_CREATED_AT]).toBeDefined();
  });
});

// ── threadIdSelector / warmPoolSelector ────────────────────────────────────────

describe("threadIdSelector", () => {
  it("builds a correct label selector for valid thread_id", () => {
    const sel = threadIdSelector("my-thread");
    expect(sel).toContain(LK_LABEL_MANAGED_BY);
    expect(sel).toContain(LK_LABEL_THREAD_ID);
    expect(sel).toContain("my-thread");
  });

  it("sanitizes thread_id in the selector", () => {
    const longId = "a".repeat(64);
    const sel = threadIdSelector(longId);
    expect(sel).not.toContain(longId);
    expect(sel).toMatch(/[0-9a-f]{12}/);
  });
});

describe("warmPoolSelector", () => {
  it("returns a selector with pool-status=warm", () => {
    const sel = warmPoolSelector();
    expect(sel).toContain("pool-status=warm");
    expect(sel).toContain(LK_LABEL_MANAGED_BY);
  });
});
