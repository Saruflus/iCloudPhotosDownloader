export interface EtaResult {
  ratePerMin: number | null; // processed items per minute
  etaSec: number | null; // seconds remaining, null if unknown
}

/** Throughput + ETA from a fixed reference point.
 *
 * `processedDelta` is items processed since `elapsedSec` ago. Keeping a single
 * reference (rather than instantaneous deltas) smooths out the bursty per-file
 * progress events. Returns nulls until there's enough signal to be meaningful. */
export function estimate(
  processedDelta: number,
  remaining: number,
  elapsedSec: number,
): EtaResult {
  if (elapsedSec <= 0 || processedDelta <= 0) {
    return { ratePerMin: null, etaSec: null };
  }
  const ratePerSec = processedDelta / elapsedSec;
  return {
    ratePerMin: ratePerSec * 60,
    etaSec: remaining > 0 ? Math.round(remaining / ratePerSec) : 0,
  };
}

/** Compact human duration: "45s", "3m 20s", "1h 04m". */
export function formatDuration(sec: number | null): string {
  if (sec == null || !isFinite(sec) || sec < 0) return "—";
  const s = Math.round(sec);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${String(m % 60).padStart(2, "0")}m`;
}

/** "~120/min" or "" when rate is unknown. */
export function formatRate(ratePerMin: number | null): string {
  if (ratePerMin == null || !isFinite(ratePerMin) || ratePerMin <= 0) return "";
  return ratePerMin >= 10 ? `~${Math.round(ratePerMin)}/min` : `~${ratePerMin.toFixed(1)}/min`;
}
