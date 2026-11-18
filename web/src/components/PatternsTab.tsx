"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import type { DashboardMeta } from "@/server/routers/overview";
import { day, describeStats, TYPE_LABEL, type FindingTypeKey } from "@/lib/shared";
import { useTRPC } from "@/trpc/client";
import { Loading, QueryError } from "./States";

const TYPES = Object.keys(TYPE_LABEL) as FindingTypeKey[];

export function PatternsTab({ meta }: { meta: DashboardMeta }) {
  const trpc = useTRPC();
  const q = useQuery(trpc.patterns.list.queryOptions());
  const [onlyReal, setOnlyReal] = useState(true);
  const [types, setTypes] = useState<Set<FindingTypeKey>>(new Set(TYPES));

  const view = useMemo(
    () => (q.data ?? []).filter((p) => types.has(p.type) && (!onlyReal || p.coherent)),
    [q.data, types, onlyReal],
  );

  if (q.isPending) return <Loading />;
  if (q.isError) return <QueryError error={q.error} />;
  if (!q.data.length) {
    return <div className="notice">No summaries yet. Run <code>python -m src.summarize</code>.</div>;
  }

  const toggle = (t: FindingTypeKey) =>
    setTypes((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });

  return (
    <div>
      <div className="toolbar">
        <label className="switch">
          <input type="checkbox" checked={onlyReal} onChange={(e) => setOnlyReal(e.target.checked)} />
          Only patterns judged real
        </label>
        <div className="chips" role="group" aria-label="Finding type">
          {TYPES.map((t) => (
            <button key={t} className="chip" aria-pressed={types.has(t)} onClick={() => toggle(t)}>
              {TYPE_LABEL[t]}
            </button>
          ))}
        </div>
      </div>
      <p className="caption" style={{ marginBottom: 14 }}>
        Showing {view.length} of {q.data.length} findings. Each explanation was written from only the
        incidents listed under it.
      </p>

      <div className="stack">
        {view.map((p) => (
          <article className="card" key={p.key}>
            <h2>{p.headline}</h2>
            <div className="badges">
              <span className="badge">{TYPE_LABEL[p.type]}</span>
              {p.coherent ? (
                <span className="badge real">✓ Real pattern</span>
              ) : (
                <span className="badge">? Likely not real</span>
              )}
              <span className="badge">{p.confidence} confidence</span>
            </div>
            <p className="caption">{describeStats(p.type, p.category, p.stats)}</p>
            <p className="prose">{p.explanation}</p>
            {p.caveats && (
              <p className="caption"><strong>Caveats:</strong> {p.caveats}</p>
            )}
            {p.llmModel !== meta.summaryModel && (
              <p className="warn">
                ⚠ Written by fallback model <code>{p.llmModel}</code>. Its verdicts have disagreed
                with <code>{meta.summaryModel}</code> before; treat as provisional.
              </p>
            )}
            <details>
              <summary>Evidence · {p.evidence.length} incidents ({p.citedCount} cited)</summary>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr><th>Cited</th><th>Date</th><th>Category</th><th>Extracted root cause</th><th>Issue</th></tr>
                  </thead>
                  <tbody>
                    {p.evidence.map((e) => (
                      <tr key={e.id}>
                        <td>{e.cited ? "✓" : ""}</td>
                        <td className="nowrap">{day(e.createdAt)}</td>
                        <td className="nowrap">{e.failureCategory ?? "—"}</td>
                        <td>{e.rootCauseSummary ?? "—"}</td>
                        <td className="nowrap">
                          {e.sourceUrl ? <a href={e.sourceUrl} target="_blank" rel="noreferrer">open ↗</a> : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          </article>
        ))}
      </div>
    </div>
  );
}
