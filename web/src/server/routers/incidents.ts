import { TRPCError } from "@trpc/server";
import { z } from "zod";
import { IN_WINDOW, publicProcedure, router } from "../trpc";

const PAGE_SIZE = 50;

/** Escape LIKE wildcards so a search for "100%" means the literal text. */
const likeTerm = (q: string) => `%${q.replace(/[\\%_]/g, (c) => `\\${c}`)}%`;

export const incidentsRouter = router({
  /** Categories that appear in extracted incidents, for the filter. */
  categories: publicProcedure.query(async ({ ctx }) =>
    (
      await ctx.db.all<{ c: string }>(
        "SELECT DISTINCT failure_category AS c FROM structured_incidents ORDER BY c",
      )
    ).map((r) => r.c),
  ),

  /** Substring search over title, report text and extracted root cause. */
  search: publicProcedure
    .input(
      z.object({
        q: z.string().trim().max(200).default(""),
        categories: z.array(z.string()).max(30).default([]),
        extractedOnly: z.boolean().default(false),
        page: z.number().int().min(0).default(0),
      }),
    )
    .query(async ({ ctx, input }) => {
      const where: string[] = [IN_WINDOW];
      const params: (string | number)[] = [];
      if (input.q) {
        // Case-insensitive (ILIKE on Postgres; SQLite's LIKE already is).
        const op = ctx.db.like;
        where.push(
          `(r.title ${op} ? ESCAPE '\\' OR r.raw_text ${op} ? ESCAPE '\\'
            OR s.root_cause_summary ${op} ? ESCAPE '\\')`,
        );
        const t = likeTerm(input.q);
        params.push(t, t, t);
      }
      if (input.categories.length) {
        where.push(`s.failure_category IN (${input.categories.map(() => "?").join(",")})`);
        params.push(...input.categories);
      }
      if (input.extractedOnly) where.push("s.incident_id IS NOT NULL");
      const from = `FROM raw_incidents r LEFT JOIN structured_incidents s ON s.incident_id = r.id
                    WHERE ${where.join(" AND ")}`;

      const [count, rows] = await Promise.all([
        ctx.db.get<{ n: number }>(`SELECT CAST(COUNT(*) AS INTEGER) AS n ${from}`, ...params),
        ctx.db.all<{
          id: number; created_at: string; title: string | null; failure_category: string | null;
          severity_label: number | null; source_url: string | null;
        }>(
          `SELECT r.id, r.created_at, r.title, s.failure_category, r.severity_label, r.source_url
           ${from} ORDER BY r.created_at DESC LIMIT ? OFFSET ?`,
          ...params, PAGE_SIZE, input.page * PAGE_SIZE,
        ),
      ]);
      return {
        total: count?.n ?? 0,
        page: input.page,
        pageSize: PAGE_SIZE,
        rows: rows.map((r) => ({
          id: r.id,
          createdAt: r.created_at,
          title: r.title,
          category: r.failure_category,
          severityLabel: r.severity_label,
          sourceUrl: r.source_url,
        })),
      };
    }),

  /** Everything known about one incident, including the report text. */
  byId: publicProcedure.input(z.object({ id: z.number().int() })).query(async ({ ctx, input }) => {
    const r = await ctx.db.get<{
      id: number; created_at: string; title: string | null; source_url: string | null;
      raw_text: string | null; severity_label: number | null; root_cause_label: string | null;
      failure_category: string | null; severity_assessment: number | null;
      root_cause_summary: string | null; affected_service: string | null; llm_model: string | null;
    }>(
      `SELECT r.id, r.created_at, r.title, r.source_url, r.raw_text, r.severity_label,
              r.root_cause_label, s.failure_category, s.severity_assessment,
              s.root_cause_summary, s.affected_service, s.llm_model
       FROM raw_incidents r LEFT JOIN structured_incidents s ON s.incident_id = r.id
       WHERE r.id = ? AND ${IN_WINDOW}`,
      input.id,
    );
    if (!r) throw new TRPCError({ code: "NOT_FOUND", message: `Incident #${input.id} not found` });
    return {
      id: r.id,
      createdAt: r.created_at,
      title: r.title,
      sourceUrl: r.source_url,
      rawText: r.raw_text,
      severityLabel: r.severity_label,
      rootCauseLabel: r.root_cause_label,
      extraction: r.failure_category
        ? {
            failureCategory: r.failure_category,
            severity: r.severity_assessment,
            rootCauseSummary: r.root_cause_summary,
            affectedService: r.affected_service,
            llmModel: r.llm_model,
          }
        : null,
    };
  }),
});
