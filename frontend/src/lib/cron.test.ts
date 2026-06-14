import { describe, expect, it } from "vitest";
import { buildCron, looksLikeCron, parseCron } from "./cron";

describe("buildCron", () => {
  it("daily at H:M", () => {
    expect(buildCron("daily", 2, 0, 6, "")).toBe("0 2 * * *");
    expect(buildCron("daily", 23, 45, 6, "")).toBe("45 23 * * *");
  });

  it("every N hours", () => {
    expect(buildCron("everyN", 2, 0, 6, "")).toBe("0 */6 * * *");
    expect(buildCron("everyN", 2, 0, 1, "")).toBe("0 */1 * * *");
  });

  it("custom passes through trimmed", () => {
    expect(buildCron("custom", 0, 0, 0, "  15 3 * * 1  ")).toBe("15 3 * * 1");
  });
});

describe("parseCron", () => {
  it("round-trips daily", () => {
    const p = parseCron("45 23 * * *");
    expect(p.mode).toBe("daily");
    expect(p.hour).toBe(23);
    expect(p.minute).toBe(45);
    expect(buildCron(p.mode, p.hour, p.minute, p.everyN, p.custom)).toBe("45 23 * * *");
  });

  it("round-trips everyN", () => {
    const p = parseCron("0 */6 * * *");
    expect(p.mode).toBe("everyN");
    expect(p.everyN).toBe(6);
    expect(buildCron(p.mode, p.hour, p.minute, p.everyN, p.custom)).toBe("0 */6 * * *");
  });

  it("falls back to custom for anything else", () => {
    const p = parseCron("15 3 * * 1");
    expect(p.mode).toBe("custom");
    expect(p.custom).toBe("15 3 * * 1");
    expect(buildCron(p.mode, p.hour, p.minute, p.everyN, p.custom)).toBe("15 3 * * 1");
  });
});

describe("looksLikeCron", () => {
  it("accepts 5-field expressions", () => {
    expect(looksLikeCron("0 2 * * *")).toBe(true);
    expect(looksLikeCron("*/15 0-6 1,15 * 1-5")).toBe(true);
  });

  it("rejects wrong field counts and junk", () => {
    expect(looksLikeCron("")).toBe(false);
    expect(looksLikeCron("0 2 * *")).toBe(false);
    expect(looksLikeCron("a b c d e")).toBe(false);
  });
});
