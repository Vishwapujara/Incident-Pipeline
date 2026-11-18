import { all } from "../db";
import { publicProcedure, router } from "../trpc";

export type FindingType = "cluster" | "changepoint" | "anomaly";
export type Confidence = "high" | "medium" | "low";

type Row = {
  finding_key: string;
  finding_type: FindingType;
  category: string | null;
  stats_json: string;
  evidence_ids_json: string;
  cited_ids_json: string;
  headline: string;
  explanation: string;
  coherent: number;
  confidence: Confidence;
  caveats: string | null;
  llm_model: string;
  generated_at: string;
};

export type EvidenceIncident = {
  id: number;
  createdAt: string;
  failureCategory: string | null;
  rootCauseSummary: string | null;
  sourceUrl: string | null;
  cited: boolean;
};

const CONF_ORDER: Record<Confidence, number> = { high: 0, medium: 1, low: 2 };

export const patternsRouter = router({
  /** Active Phase 7 findings with their evidence incidents, best first. */
  list: publicProcedure.query(({ ctx }) => {
    const rows = all<Row>(ctx.db, "SELECT * FROM pattern_summaries WHERE active = 1");

    const evidenceIds = new Set<number>();
    for (const r of rows) for (const id of JSON.parse(r.evidence_ids_json) as number[]) evidenceIds.add(id);
    const incidents = new Map<number, Omit<EvidenceIncident, "cited">>();
    if (evidenceIds.size) {
      const ids = [...evidenceIds];
      const found = all<{
        id: number; created_at: string; failure_category: string | null;
        root_cause_summary: string | null; source_url: string | null;
      }>(
        ctx.db,
        `SELECT r.id, r.created_at, r.source_url, s.failure_category, s.root_cause_summary
         FROM raw_incidents r LEFT JOIN structured_incidents s ON s.incident_id = r.id
         WHERE r.id IN (${ids.map(() => "?").join(",")})`,
        ...ids,
      );
      for (const f of found) {
        incidents.set(f.id, {
          id: f.id, createdAt: f.created_at, failureCategory: f.failure_category,
          rootCauseSummary: f.root_cause_summary, sourceUrl: f.source_url,
        });
      }
    }

    return rows
      .map((r) => {
        const cited = new Set(JSON.parse(r.cited_ids_json) as number[]);
        const evidence = (JSON.parse(r.evidence_ids_json) as number[])
          .map((id) => incidents.get(id))
          .filter((x): x is Omit<EvidenceIncident, "cited"> => !!x)
          .map((x) => ({ ...x, cited: cited.has(x.id) }));
        return {
          key: r.finding_key,
          type: r.finding_type,
          category: r.category,
          stats: JSON.parse(r.stats_json) as Record<string, unknown>,
          headline: r.headline,
          explanation: r.explanation,
          caveats: r.caveats,
          coherent: r.coherent === 1,
          confidence: r.confidence,
          llmModel: r.llm_model,
          generatedAt: r.generated_at,
          citedCount: cited.size,
          evidence,
        };
      })
      .sort(
        (a, b) =>
          Number(b.coherent) - Number(a.coherent) ||
          CONF_ORDER[a.confidence] - CONF_ORDER[b.confidence],
      );
  }),
});
