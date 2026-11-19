import { IN_WINDOW, publicProcedure, router } from "../trpc";

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
  get: publicProcedure.query(async ({ ctx }) => {
    const m = Object.fromEntries(
      (await ctx.db.all<{ key: string; value: string }>("SELECT key, value FROM dashboard_meta")).map(
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

    const [counts, findings] = await Promise.all([
      ctx.db.get<{ total: number; extracted: number }>(
        `SELECT CAST(COUNT(*) AS INTEGER) AS total, CAST(COUNT(s.incident_id) AS INTEGER) AS extracted
         FROM raw_incidents r LEFT JOIN structured_incidents s ON s.incident_id = r.id
         WHERE ${IN_WINDOW}`,
      ),
      ctx.db.get<{ active: number; coherent: number }>(
        `SELECT CAST(COUNT(*) AS INTEGER) AS active, CAST(COALESCE(SUM(coherent), 0) AS INTEGER) AS coherent
         FROM pattern_summaries WHERE active = 1`,
      ),
    ]);

    return { meta, ...(counts ?? { total: 0, extracted: 0 }), ...(findings ?? { active: 0, coherent: 0 }) };
  }),
});
