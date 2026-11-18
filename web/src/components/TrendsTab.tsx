"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import type { DashboardMeta } from "@/server/routers/overview";
import { useTRPC } from "@/trpc/client";
import { TrendChart, TrendLegend } from "./TrendChart";
import { Loading, QueryError } from "./States";

const STATUS_NOTE = {
  evaluated: "",
  too_sparse: " · too sparse to evaluate",
  insufficient_weeks: " · not enough weeks",
} as const;

export function TrendsTab({ meta }: { meta: DashboardMeta }) {
  const trpc = useTRPC();
  const q = useQuery(trpc.trends.get.queryOptions());
  const [showSparse, setShowSparse] = useState(false);

  if (q.isPending) return <Loading />;
  if (q.isError) return <QueryError error={q.error} />;
  const { weeks, total, categories } = q.data;
  if (!weeks.length) {
    return <div className="notice">No aggregated data yet. Run <code>python -m src.aggregate</code>.</div>;
  }

  const shown = categories.filter((c) => showSparse || c.status === "evaluated");

  return (
    <div className="stack">
      <p className="caption">
        Weekly incident counts per category. <strong>Shaded weeks are not used for detection</strong>:
        weeks before {meta.coverageStartWeek ?? "—"} are less than{" "}
        {Math.round(meta.minWeekExtractionCoverage * 100)}% extracted, and the newest{" "}
        {meta.excludeRecentWeeks} weeks may still have open incidents. Detection compares each category
        with that week&apos;s <strong>total</strong> incident volume, so a busy week for everything is
        flagged once, in the All incidents chart, not in every category. Dashed lines mark a change in
        the category&apos;s share of incidents; open circles mark weeks far above what the total volume
        predicts.
      </p>
      <TrendLegend />

      {total && total.status === "evaluated" && (
        <div className="card">
          <h3 className="chart-title">
            All incidents{" "}
            <span className="note">
              · typical week ≈ {Math.round(total.baseline ?? 0)} (a surge here is not repeated in the
              per-category charts)
            </span>
          </h3>
          <TrendChart weeks={weeks} series={total} expectedLabel="Typical week" />
        </div>
      )}

      <label className="switch">
        <input type="checkbox" checked={showSparse} onChange={(e) => setShowSparse(e.target.checked)} />
        Include categories too sparse to evaluate
      </label>

      <div className="grid-2">
        {shown.map((s) => (
          <div className="card" key={s.key}>
            <h3 className="chart-title">
              {s.key}{" "}
              <span className="note">
                · {s.total} incidents in analysed weeks{STATUS_NOTE[s.status]}
              </span>
            </h3>
            <TrendChart weeks={weeks} series={s} />
          </div>
        ))}
      </div>
    </div>
  );
}
