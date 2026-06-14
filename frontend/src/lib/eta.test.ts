import { describe, expect, it } from "vitest";
import { estimate, formatDuration, formatRate } from "./eta";

describe("estimate", () => {
  it("computes rate and ETA from a reference window", () => {
    // 100 processed in 50s → 2/s = 120/min; 200 remaining → 100s
    expect(estimate(100, 200, 50)).toEqual({ ratePerMin: 120, etaSec: 100 });
  });

  it("ETA is 0 when nothing remains", () => {
    expect(estimate(50, 0, 25).etaSec).toBe(0);
  });

  it("returns nulls before there is signal", () => {
    expect(estimate(0, 100, 10)).toEqual({ ratePerMin: null, etaSec: null });
    expect(estimate(5, 100, 0)).toEqual({ ratePerMin: null, etaSec: null });
  });
});

describe("formatDuration", () => {
  it("formats seconds/minutes/hours", () => {
    expect(formatDuration(45)).toBe("45s");
    expect(formatDuration(200)).toBe("3m 20s");
    expect(formatDuration(3845)).toBe("1h 04m");
  });

  it("handles unknown/invalid", () => {
    expect(formatDuration(null)).toBe("—");
    expect(formatDuration(-5)).toBe("—");
    expect(formatDuration(Infinity)).toBe("—");
  });
});

describe("formatRate", () => {
  it("rounds high rates, shows a decimal for slow ones", () => {
    expect(formatRate(123.7)).toBe("~124/min");
    expect(formatRate(2.5)).toBe("~2.5/min");
  });

  it("empty when unknown", () => {
    expect(formatRate(null)).toBe("");
    expect(formatRate(0)).toBe("");
  });
});
