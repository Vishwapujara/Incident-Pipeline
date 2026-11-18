"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Bar, BarChart, CartesianGrid, Cell, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { DashboardMeta } from "@/server/routers/overview";
import { shortDate } from "@/lib/shared";
import { useThemeColors } from "@/lib/useThemeColors";
import { useTRPC } from "@/trpc/client";
import { Loading, QueryError } from "./States";

export function CoverageTab({ meta }: { meta: DashboardMeta }) {
  const trpc = useTRPC();
  const q = useQuery(trpc.coverage.get.queryOptions());
  const c = useThemeColors();

  if (q.isPending) return <Loading />;
  if (q.isError) return <QueryError error={q.error} />;
  if (!q.data.length) return <div className="notice">No incidents in the window.</div>;
  const rows = q.data;
  const threshold = meta.minWeekExtractionCoverage * 100;

  return (
    <div className="stack">
      <p className="caption">
        Share of each week&apos;s incidents that have been extracted. Detection only uses the newest
        unbroken run of weeks at ≥{Math.round(threshold)}% (minus the newest {meta.excludeRecentWeeks});
        everything else would show up as a false drop. Older weeks fill in as daily extraction batches run.
      </p>
      <div className="card">
        <div className="legend">
          <span><span className="sw" style={{ background: "var(--series-1)" }} />Used for detection</span>
          <span><span className="sw" style={{ background: "var(--bar-off)" }} />Not used for detection</span>
          <span><span className="sw dash" />{Math.round(threshold)}% threshold</span>
        </div>
        <div style={{ width: "100%", height: 280 }}>
          <ResponsiveContainer>
            <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: -12 }} barCategoryGap={1}>
              <CartesianGrid vertical={false} stroke={c.grid} strokeOpacity={0.7} />
              <XAxis
                dataKey="week" tickFormatter={(w: string) => shortDate(w)} minTickGap={28}
                tick={{ fill: c.text, fontSize: 11 }} tickLine={false} axisLine={{ stroke: c.grid }}
              />
              <YAxis
                domain={[0, 100]} ticks={[0, 25, 50, 75, 100]} tickFormatter={(v: number) => `${v}%`}
                tick={{ fill: c.text, fontSize: 11 }} tickLine={false} axisLine={false} width={48}
              />
              <Bar dataKey="pct" radius={[4, 4, 0, 0]} isAnimationActive={false}>
                {rows.map((r) => (
                  <Cell key={r.week} fill={r.usedForDetection ? c.series : c.barOff} />
                ))}
              </Bar>
              <ReferenceLine y={threshold} stroke={c.annotation} strokeDasharray="4 3" />
              <Tooltip
                cursor={{ fill: c.grid, fillOpacity: 0.5 }}
                content={({ active, payload }) => {
                  const r = payload?.[0]?.payload as (typeof rows)[number] | undefined;
                  if (!active || !r) return null;
                  return (
                    <div className="tooltip">
                      <div className="t">Week of {shortDate(r.week, true)}</div>
                      <div>{r.extracted} of {r.raw} extracted ({Math.round(r.pct)}%)</div>
                      <div className="r">{r.usedForDetection ? "Used for detection" : "Not used for detection"}</div>
                    </div>
                  );
                }}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <details>
        <summary>Table view</summary>
        <div className="table-wrap" style={{ maxHeight: 420, overflowY: "auto" }}>
          <table>
            <thead>
              <tr><th>Week of</th><th className="num">Incidents</th><th className="num">Extracted</th><th className="num">% extracted</th><th>Status</th></tr>
            </thead>
            <tbody>
              {[...rows].reverse().map((r) => (
                <tr key={r.week}>
                  <td className="nowrap">{r.week}</td>
                  <td className="num">{r.raw}</td>
                  <td className="num">{r.extracted}</td>
                  <td className="num">{Math.round(r.pct)}%</td>
                  <td>{r.usedForDetection ? "used for detection" : "not used for detection"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
