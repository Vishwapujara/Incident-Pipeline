import { all, get } from "../db";
import { publicProcedure, router } from "../trpc";

export type DashboardMeta = {
  dataCutoffDate: string;
  coverageStartWeek: string | null;
  excludeRecentWeeks: number;
  minWeekExtractionCoverage: number;
  summaryModel: string;
  generatedAt: string;
};

export const overviewRouter = router({
  /** Header metrics + the pipeline settings the UI explains. */
  get: publicProcedure.query(({ ctx }) => {
    const m = Object.fromEntries(
      all<{ key: string; value: string }>(ctx.db, "SELECT key, value FROM dashboard_meta").map(
        (r) => [r.key, r.value],
      ),
    );
    const meta: DashboardMeta = {
      dataCutoffDate: m.data_cutoff_date ?? "",
      coverageStartWeek: m.coverage_start_week || null,
      excludeRecentWeeks: Number(m.exclude_recent_weeks ?? 4),
      minWeekExtractionCoverage: Number(m.min_week_extraction_coverage ?? 0.9),
      summaryModel: m.summary_model ?? "",
      generatedAt: m.generated_at ?? "",
    };

    const counts = get<{ total: number; extracted: number }>(
      ctx.db,
      `SELECT COUNT(*) AS total, COUNT(s.incident_id) AS extracted
       FROM raw_incidents r LEFT JOIN structured_incidents s ON s.incident_id = r.id`,
    ) ?? { total: 0, extracted: 0 };
    const findings = get<{ active: number; coherent: number }>(
      ctx.db,
      `SELECT COUNT(*) AS active, COALESCE(SUM(coherent), 0) AS coherent
       FROM pattern_summaries WHERE active = 1`,
    ) ?? { active: 0, coherent: 0 };

    return { meta, ...counts, ...findings };
  }),
});
