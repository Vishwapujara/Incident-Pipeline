/** Constants and formatting shared by the server routers and the UI. */

export const TOTAL_KEY = "__total__";

export const TYPE_LABEL = {
  cluster: "Recurring root cause",
  changepoint: "Change in share of incidents",
  anomaly: "Unusual week",
} as const;

export type FindingTypeKey = keyof typeof TYPE_LABEL;

const pct = (x: number) => `${Math.round(x * 100)}%`;
const n = (x: unknown) => Number(x);

/** One-line description of the numbers a finding is based on. */
export function describeStats(type: FindingTypeKey, category: string | null, s: Record<string, unknown>): string {
  if (type === "cluster") {
    return `${n(s.size)} incidents · categories: ${(s.categories as string[] | undefined)?.join(", ") ?? "—"}`;
  }
  if (type === "changepoint") {
    const share =
      "share_before" in s ? ` (share ${pct(n(s.share_before))} → ${pct(n(s.share_after))})` : "";
    return `${category} · from week of ${s.change_week}: ${s.weekly_mean_before}/week → ${s.weekly_mean_after}/week${share}`;
  }
  if ("typical_week" in s) {
    return `${category} · week of ${s.week}: ${s.count} incidents (typical week ≈ ${Math.round(n(s.typical_week))})`;
  }
  if ("expected_from_week_volume" in s) {
    return `${category} · week of ${s.week}: ${s.count} incidents, ≈${n(s.expected_from_week_volume).toFixed(1)} expected from that week's ${s.all_incidents_that_week} total`;
  }
  return `${category} · week of ${s.week}: ${s.count} incidents (usual ≈ ${s.category_weekly_mean}/week, z = ${s.z_score})`;
}

export const day = (iso: string) => iso.slice(0, 10);

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
/** "2026-05-11" → "May 11" (UTC, no timezone drift). */
export function shortDate(isoDay: string, withYear = false): string {
  const [y, m, d] = isoDay.slice(0, 10).split("-");
  return `${MONTHS[Number(m) - 1]} ${Number(d)}${withYear ? `, ${y}` : ""}`;
}
