"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useTRPC } from "@/trpc/client";
import { CoverageTab } from "./CoverageTab";
import { PatternsTab } from "./PatternsTab";
import { SearchTab } from "./SearchTab";
import { Loading, QueryError } from "./States";
import { TrendsTab } from "./TrendsTab";

const TABS = [
  { id: "patterns", label: "Patterns" },
  { id: "trends", label: "Trends" },
  { id: "search", label: "Search incidents" },
  { id: "coverage", label: "Data coverage" },
] as const;
type TabId = (typeof TABS)[number]["id"];
const isTab = (s: string): s is TabId => TABS.some((t) => t.id === s);

/** Active tab lives in the URL hash so a view can be linked. */
function useHashTab(): [TabId, (t: TabId) => void] {
  const [tab, setTab] = useState<TabId>("patterns");
  useEffect(() => {
    const read = () => {
      const h = window.location.hash.slice(1);
      if (isTab(h)) setTab(h);
    };
    read();
    window.addEventListener("hashchange", read);
    return () => window.removeEventListener("hashchange", read);
  }, []);
  return [tab, (t) => { history.replaceState(null, "", `#${t}`); setTab(t); }];
}

export function Dashboard() {
  const trpc = useTRPC();
  const overview = useQuery(trpc.overview.get.queryOptions());
  const [tab, setTab] = useHashTab();

  return (
    <main className="page">
      <h1>Incident postmortem patterns</h1>
      <p className="caption">
        GitLab production incidents
        {overview.data && <> · window from {overview.data.meta.dataCutoffDate}</>}
        {" "}· read-only view of the pipeline
        {overview.data?.meta.generatedAt && <> · data as of {overview.data.meta.generatedAt.slice(0, 10)}</>}
      </p>

      {overview.isPending ? (
        <div style={{ marginTop: 24 }}><Loading label="Loading the latest data snapshot…" /></div>
      ) : overview.isError ? (
        <div style={{ marginTop: 24 }}><QueryError error={overview.error} /></div>
      ) : (
        <>
          <section className="metrics" aria-label="Summary">
            <Metric label="Incidents in window" value={overview.data.total.toLocaleString()} />
            <Metric
              label="Extracted" value={overview.data.extracted.toLocaleString()}
              sub={overview.data.total ? `${Math.round((overview.data.extracted / overview.data.total) * 100)}% of window` : undefined}
            />
            <Metric label="Active findings" value={String(overview.data.active)} />
            <Metric label="Judged real patterns" value={String(overview.data.coherent)} />
          </section>

          <nav className="tabs" role="tablist">
            {TABS.map((t) => (
              <button
                key={t.id} role="tab" className="tab" aria-selected={tab === t.id}
                onClick={() => setTab(t.id)}
              >
                {t.label}
              </button>
            ))}
          </nav>

          <div role="tabpanel">
            {tab === "patterns" && <PatternsTab meta={overview.data.meta} />}
            {tab === "trends" && <TrendsTab meta={overview.data.meta} />}
            {tab === "search" && <SearchTab />}
            {tab === "coverage" && <CoverageTab meta={overview.data.meta} />}
          </div>
        </>
      )}
    </main>
  );
}

function Metric({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="metric">
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}
