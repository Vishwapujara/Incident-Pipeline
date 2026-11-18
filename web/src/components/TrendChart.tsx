"use client";

import { useMemo } from "react";
import {
  CartesianGrid, Line, LineChart, ReferenceArea, ReferenceDot, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { RouterOutputs } from "@/server/root";
import { shortDate } from "@/lib/shared";
import { useThemeColors } from "@/lib/useThemeColors";

type Series = NonNullable<RouterOutputs["trends"]["get"]["total"]>;

const DAY = 86_400_000;
const ts = (d: string) => Date.parse(`${d}T00:00:00Z`);
const isoDay = (t: number) => new Date(t).toISOString().slice(0, 10);

type Point = { t: number; week: string; count: number; analysed: boolean; expected?: number };

export function TrendChart({
  weeks, series, expectedLabel = "Expected from week's volume",
}: { weeks: string[]; series: Series; expectedLabel?: string }) {
  const c = useThemeColors();

  const { data, excludedRuns, anomalies } = useMemo(() => {
    const analysed = new Set(series.analysedWeeks);
    const anomalyByWeek = new Map(series.anomalies.map((a) => [a.week, a]));
    const data: Point[] = weeks.map((w, i) => ({
      t: ts(w), week: w, count: series.counts[i] ?? 0, analysed: analysed.has(w),
      expected: anomalyByWeek.get(w)?.expected,
    }));
    // Merge consecutive non-analysed weeks into one shaded band each.
    const excludedRuns: { x1: number; x2: number }[] = [];
    for (const p of data) {
      if (p.analysed) continue;
      const last = excludedRuns[excludedRuns.length - 1];
      if (last && last.x2 === p.t) last.x2 = p.t + 7 * DAY;
      else excludedRuns.push({ x1: p.t, x2: p.t + 7 * DAY });
    }
    return { data, excludedRuns, anomalies: series.anomalies };
  }, [weeks, series]);

  const first = data[0]?.t ?? 0;
  const last = data[data.length - 1]?.t ?? 0;

  return (
    <div style={{ width: "100%", height: 210 }}>
      <ResponsiveContainer>
        <LineChart data={data} margin={{ top: 10, right: 12, bottom: 0, left: -12 }}>
          <CartesianGrid vertical={false} stroke={c.grid} strokeOpacity={0.7} />
          {excludedRuns.map((r) => (
            <ReferenceArea
              key={r.x1} x1={r.x1} x2={Math.min(r.x2, last)} ifOverflow="hidden"
              fill={c.shade} fillOpacity={0.12} stroke="none"
            />
          ))}
          <XAxis
            dataKey="t" type="number" scale="time" domain={[first, last]}
            tickFormatter={(t: number) => shortDate(isoDay(t))}
            tick={{ fill: c.text, fontSize: 11 }} tickLine={false} axisLine={{ stroke: c.grid }}
            minTickGap={28}
          />
          <YAxis
            allowDecimals={false} tick={{ fill: c.text, fontSize: 11 }} tickLine={false} axisLine={false}
            width={44}
          />
          {series.changepoints.map((w, i) => (
            <ReferenceLine
              key={w} x={ts(w)} stroke={c.annotation} strokeDasharray="4 3" strokeWidth={1.5}
              // Alternate bottom/top so labels of nearby change-points don't collide.
              label={{
                value: "rate change", position: i % 2 ? "insideTopLeft" : "insideBottomLeft",
                fill: c.annotation, fontSize: 11,
              }}
            />
          ))}
          <Line
            dataKey="count" stroke={c.series} strokeWidth={2} isAnimationActive={false}
            dot={{ r: 3, fill: c.series, stroke: c.surface, strokeWidth: 1 }}
            activeDot={{ r: 5, fill: c.series, stroke: c.surface, strokeWidth: 2 }}
          />
          {anomalies.map((a) => (
            <ReferenceDot
              key={a.week} x={ts(a.week)} y={a.count} r={8} fill="none"
              stroke={c.annotation} strokeWidth={2} ifOverflow="visible"
            />
          ))}
          <Tooltip
            cursor={{ stroke: c.annotation, strokeWidth: 1 }}
            content={({ active, payload }) => {
              const p = payload?.[0]?.payload as Point | undefined;
              if (!active || !p) return null;
              return (
                <div className="tooltip">
                  <div className="t">Week of {shortDate(p.week, true)}</div>
                  <div>{p.count} incidents</div>
                  {p.expected !== undefined && (
                    <div className="r">Unusual week · {expectedLabel}: {p.expected.toFixed(1)}</div>
                  )}
                  {!p.analysed && <div className="r">Not analysed</div>}
                </div>
              );
            }}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

export function TrendLegend() {
  return (
    <div className="legend" aria-hidden>
      <span><span className="sw line" style={{ background: "var(--series-1)" }} />Incidents per week</span>
      <span><span className="sw" style={{ background: "var(--shade)", opacity: 0.25 }} />Not analysed</span>
      <span><span className="sw dash" />Change in share</span>
      <span><span className="sw ring" />Unusual week</span>
    </div>
  );
}
