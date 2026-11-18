import { all } from "../db";
import { publicProcedure, router } from "../trpc";

export const coverageRouter = router({
  /**
   * Share of each week's incidents that have been extracted, starting four
   * weeks before the first extracted week (older weeks are all zero).
   */
  get: publicProcedure.query(({ ctx }) => {
    const rows = all<{
      week_start: string; raw: number; extracted: number; coverage: number; used_for_detection: number;
    }>(ctx.db, "SELECT * FROM week_coverage ORDER BY week_start");
    const first = rows.findIndex((r) => r.extracted > 0);
    return rows.slice(Math.max(0, (first === -1 ? 0 : first) - 4)).map((r) => ({
      week: r.week_start,
      raw: r.raw,
      extracted: r.extracted,
      pct: r.coverage * 100,
      usedForDetection: r.used_for_detection === 1,
    }));
  }),
});
