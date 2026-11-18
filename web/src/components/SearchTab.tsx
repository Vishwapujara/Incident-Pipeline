"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { day } from "@/lib/shared";
import { useTRPC } from "@/trpc/client";
import { Loading, QueryError } from "./States";

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

export function SearchTab() {
  const trpc = useTRPC();
  const [text, setText] = useState("");
  const q = useDebounced(text, 250);
  const [categories, setCategories] = useState<string[]>([]);
  const [extractedOnly, setExtractedOnly] = useState(false);
  const [page, setPage] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);

  // Any filter change starts again from the first page.
  useEffect(() => setPage(0), [q, categories, extractedOnly]);

  const cats = useQuery(trpc.incidents.categories.queryOptions());
  const res = useQuery({
    ...trpc.incidents.search.queryOptions({ q, categories, extractedOnly, page }),
    placeholderData: keepPreviousData,
  });

  const toggleCat = (c: string) =>
    setCategories((prev) => (prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c]));

  const pages = res.data ? Math.max(1, Math.ceil(res.data.total / res.data.pageSize)) : 1;

  return (
    <div className="stack">
      <div className="toolbar" style={{ marginBottom: 0 }}>
        <input
          className="input" style={{ flex: "1 1 320px" }} type="search" value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Search title, report text and extracted root cause, e.g. pgbouncer, migration lock, redis"
          aria-label="Search incidents"
        />
        <label className="switch">
          <input type="checkbox" checked={extractedOnly} onChange={(e) => setExtractedOnly(e.target.checked)} />
          Extracted only
        </label>
      </div>
      {cats.data && (
        <div className="chips" role="group" aria-label="Category">
          {cats.data.map((c) => (
            <button key={c} className="chip" aria-pressed={categories.includes(c)} onClick={() => toggleCat(c)}>
              {c}
            </button>
          ))}
        </div>
      )}

      {res.isPending ? (
        <Loading />
      ) : res.isError ? (
        <QueryError error={res.error} />
      ) : (
        <>
          <div className="toolbar" style={{ justifyContent: "space-between", marginBottom: 0 }}>
            <p className="caption">
              {res.data.total.toLocaleString()} incidents
              {res.isFetching ? " · updating…" : ""}
            </p>
            <div className="toolbar" style={{ marginBottom: 0, gap: 8 }}>
              <button className="btn" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>← Newer</button>
              <span className="caption">Page {page + 1} of {pages}</span>
              <button className="btn" disabled={page + 1 >= pages} onClick={() => setPage((p) => p + 1)}>Older →</button>
            </div>
          </div>
          <div className="table-wrap" style={{ maxHeight: 440, overflowY: "auto" }}>
            <table>
              <thead>
                <tr><th>Date</th><th>Title</th><th>Category</th><th>GitLab severity</th><th>Issue</th></tr>
              </thead>
              <tbody>
                {res.data.rows.map((r) => (
                  <tr
                    key={r.id}
                    className={`clickable${selected === r.id ? " selected" : ""}`}
                    onClick={() => setSelected(r.id)}
                    tabIndex={0}
                    onKeyDown={(e) => e.key === "Enter" && setSelected(r.id)}
                  >
                    <td className="nowrap">{day(r.createdAt)}</td>
                    <td>{r.title}</td>
                    <td className="nowrap">{r.category ?? "— not extracted yet"}</td>
                    <td className="nowrap">{r.severityLabel != null ? `S${r.severityLabel}` : "—"}</td>
                    <td className="nowrap">
                      {r.sourceUrl ? (
                        <a href={r.sourceUrl} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>open ↗</a>
                      ) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {selected != null ? <IncidentDetail id={selected} /> : <p className="caption">Select a row to see its details.</p>}
    </div>
  );
}

function IncidentDetail({ id }: { id: number }) {
  const trpc = useTRPC();
  const q = useQuery(trpc.incidents.byId.queryOptions({ id }));
  if (q.isPending) return <Loading />;
  if (q.isError) return <QueryError error={q.error} />;
  const inc = q.data;
  const x = inc.extraction;

  return (
    <article className="card">
      <h2>{inc.title}</h2>
      <p className="caption">
        #{inc.id} · {day(inc.createdAt)}
        {inc.sourceUrl && <> · <a href={inc.sourceUrl} target="_blank" rel="noreferrer">open on GitLab ↗</a></>}
      </p>
      {x ? (
        <>
          <div className="detail-grid">
            <Field label="Category (LLM)" value={x.failureCategory} />
            <Field label="Severity (LLM)" value={x.severity != null ? `S${x.severity}` : "—"} />
            <Field label="Severity (GitLab)" value={inc.severityLabel != null ? `S${inc.severityLabel}` : "—"} />
            <Field label="Root cause (GitLab)" value={inc.rootCauseLabel ?? "—"} />
          </div>
          <p className="prose"><strong>Extracted root cause:</strong> {x.rootCauseSummary}</p>
          <p className="caption">
            Affected service: {x.affectedService ?? "—"} · extracted by <code>{x.llmModel}</code>
          </p>
        </>
      ) : (
        <div className="notice" style={{ margin: "12px 0" }}>
          Not extracted yet. It will be picked up by a future <code>python -m src.extract</code> batch.
        </div>
      )}
      <details>
        <summary>Full report text</summary>
        <pre className="report">{inc.rawText || "(empty)"}</pre>
      </details>
    </article>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="metric">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
    </div>
  );
}
