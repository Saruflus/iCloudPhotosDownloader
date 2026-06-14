export interface CronParts {
  mode: "daily" | "everyN" | "custom";
  minute: number;
  hour: number;
  everyN: number;
  custom: string;
}

export function buildCron(mode: string, hour: number, minute: number, everyN: number, custom: string): string {
  if (mode === "daily") return `${minute} ${hour} * * *`;
  if (mode === "everyN") return `0 */${everyN} * * *`;
  return custom.trim();
}

export function parseCron(c: string): CronParts {
  const daily = c.match(/^(\d{1,2})\s+(\d{1,2})\s+\*\s+\*\s+\*$/);
  if (daily) return { mode: "daily", minute: +daily[1], hour: +daily[2], everyN: 6, custom: c };
  const en = c.match(/^0\s+\*\/(\d{1,2})\s+\*\s+\*\s+\*$/);
  if (en) return { mode: "everyN", minute: 0, hour: 2, everyN: +en[1], custom: c };
  return { mode: "custom", minute: 0, hour: 2, everyN: 6, custom: c };
}

/** Light validity check for a 5-field cron expression (UI gating only —
 *  the backend scheduler stays the real validator). */
export function looksLikeCron(c: string): boolean {
  const fields = c.trim().split(/\s+/);
  return fields.length === 5 && fields.every((f) => /^[\d*,/-]+$/.test(f));
}
