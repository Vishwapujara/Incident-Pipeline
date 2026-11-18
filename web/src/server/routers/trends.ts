import { all } from "../db";
import { publicProcedure, router } from "../trpc";

export type DetectionStatus = "evaluated" | "too_sparse" | "insufficient_weeks";
export type Anomaly = { week: string; count: number; score: number; expected: number };

export type Series = {
  key: string; // failure_category, or "__total__"
  status: DetectionStatus;
  total: number;
  baseline: number | null;
  /** Aligned with `weeks`: incidents per week (0 where none). */
  counts: number[];
  /** Weeks detection actually used; everything else is shaded. */
  analysedWeeks: string[];
  anomalies: Anomaly[];
  changepoints: string[];
};

export { TOTAL_KEY } from "@/lib/shared";
import { TOTAL_KEY } from "@/lib/shared";

const DAY = 86_400_000;

/** Every Monday from first to last, so zero-incident weeks aren't skipped. */
function weekRange(first: string, last: string): string[] {
  const out: string[] = [];
  for (let t = Date.parse(`${first}T00:00:00Z`); t <= Date.parse(`${last}T00:00:00Z`); t += 7 * DAY) {
    out.push(new Date(t).toISOString().slice(0, 10));
  }
  return out;
}

export const trendsRouter = router({
  /**
   * Weekly counts per category and in total, with Phase 5's results
   * (precomputed in Python by export_snapshot) attached.
   */
  get: publicProcedure.query(({ ctx }) => {
    const counts = all<{ week_start: string; failure_category: string; count: number }>(
      ctx.db,
      "SELECT week_start, failure_category, count FROM weekly_category_counts ORDER BY week_start",
    );
    if (!counts.length) return { weeks: [] as string[], total: null, categories: [] as Series[] };

    const weeks = weekRange(counts[0]!.week_start, counts[counts.length - 1]!.week_start);
    const idx = new Map(weeks.map((w, i) => [w, i]));
    const byCat = new Map<string, number[]>();
    const totals = new Array<number>(weeks.length).fill(0);
    for (const c of counts) {
      const i = idx.get(c.week_start);
      if (i === undefined) continue;
      let series = byCat.get(c.failure_category);
      if (!series) byCat.set(c.failure_category, (series = new Array<number>(weeks.length).fill(0)));
      series[i] = (series[i] ?? 0) + c.count;
      totals[i] = (totals[i] ?? 0) + c.count;
    }

    const det = new Map(
      all<{
        series: string; status: DetectionStatus; total: number; baseline: number | null;
        analysed_weeks_json: string; anomalies_json: string; changepoints_json: string;
      }>(ctx.db, "SELECT * FROM detection_results").map((d) => [d.series, d]),
    );

    const build = (key: string, series: number[]): Series => {
      const d = det.get(key);
      return {
        key,
        status: d?.status ?? "insufficient_weeks",
        total: d?.total ?? 0,
        baseline: d?.baseline ?? null,
        counts: series,
        analysedWeeks: d ? (JSON.parse(d.analysed_weeks_json) as string[]) : [],
        anomalies: d ? (JSON.parse(d.anomalies_json) as Anomaly[]) : [],
        changepoints: d ? (JSON.parse(d.changepoints_json) as string[]) : [],
      };
    };

    const categories = [...byCat.entries()]
      .map(([cat, series]) => build(cat, series))
      .sort((a, b) => b.total - a.total || a.key.localeCompare(b.key));

    return { weeks, total: build(TOTAL_KEY, totals), categories };
  }),
});
