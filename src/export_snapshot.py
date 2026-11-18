"""
Exports a small, read-only copy of the database for the dashboard.

The working database (data/processed/incidents.db, ~90 MB) is not in git.
The dashboard (web/, Next.js) reads only this snapshot, locally and when
hosted, so it is the single contract between the Python pipeline and the
TypeScript app. It holds:
  - incidents inside the data window, with report text cut to
    RAW_TEXT_CHARS (the full text is one click away on GitLab)
  - the extracted fields, weekly counts and active pattern summaries
  - the output of Phase 5 detection (detection_results), per-week
    extraction coverage (week_coverage) and the config values the
    dashboard explains (dashboard_meta). These are computed here, in
    Python, so the statistics (ruptures/PELT, residuals, censoring
    rules) live in one language and the web app never re-derives them.

The daily job (scripts/daily.sh) runs this after poll and pushes the
file to the dashboard-data branch, which the hosted app downloads.

Usage:
    python -m src.export_snapshot
"""

import json
import os
import sqlite3
from datetime import datetime, timezone

from src.config import (DATA_CUTOFF_DATE, DB_PATH, EXCLUDE_RECENT_WEEKS,
                        MIN_WEEK_EXTRACTION_COVERAGE, SUMMARY_MODEL)
from src.detect_patterns import (load_series, evaluate_category, evaluate_total,
                                 weekly_coverage, coverage_start_week)

SNAPSHOT_PATH = os.path.join(os.path.dirname(__file__), "..", "snapshot", "dashboard.db")
RAW_TEXT_CHARS = 3000
TRUNCATION_NOTE = "\n\n[... truncated - open the issue on GitLab for the full report]"
TOTAL_KEY = "__total__"


def _detection_rows(conn):
    """One row per series (each category + total volume), as the
    dashboard's Trends tab needs it."""
    analysed = load_series(conn)  # coverage-filtered: what detection uses
    if analysed.empty:
        return []
    rows = []
    results = [(c, evaluate_category(analysed, c))
               for c in sorted(analysed["failure_category"].unique())]
    results.append((TOTAL_KEY, evaluate_total(analysed)))
    for key, r in results:
        rows.append((
            key, r["status"], int(r["total"]), int(r["usable_weeks"]),
            r.get("baseline"),
            json.dumps([str(d.date()) for d in r["usable"].index]),
            json.dumps([{"week": str(w), "count": int(c), "score": float(s),
                         "expected": float(e)} for w, c, s, e in r["anomalies"]]),
            json.dumps([str(d) for d in r["changepoints"]]),
        ))
    return rows


def _coverage_rows(conn):
    cov = weekly_coverage(conn)
    if cov.empty:
        return [], None
    start = coverage_start_week(conn)
    weeks = list(cov.index)
    # Same rule as detection: from the coverage start on, minus the newest
    # EXCLUDE_RECENT_WEEKS (right-censoring).
    recent = set(weeks[-EXCLUDE_RECENT_WEEKS:])
    rows = []
    for week, r in cov.iterrows():
        used = start is not None and week.strftime("%Y-%m-%d") >= start and week not in recent
        rows.append((week.strftime("%Y-%m-%d"), int(r["raw"]), int(r["extracted"]),
                     float(r["coverage"]), int(used)))
    return rows, start


def _write_derived(out):
    """Detection output + config, computed against the full working DB."""
    out.execute("""CREATE TABLE detection_results (
        series TEXT PRIMARY KEY,          -- failure_category, or '__total__'
        status TEXT NOT NULL,             -- evaluated | too_sparse | insufficient_weeks
        total INTEGER NOT NULL,           -- incidents in analysed weeks
        usable_weeks INTEGER NOT NULL,
        baseline REAL,                    -- total series only: typical week
        analysed_weeks_json TEXT NOT NULL,
        anomalies_json TEXT NOT NULL,     -- [{week, count, score, expected}]
        changepoints_json TEXT NOT NULL   -- [week, ...]
    )""")
    out.execute("""CREATE TABLE week_coverage (
        week_start TEXT PRIMARY KEY,
        raw INTEGER NOT NULL,
        extracted INTEGER NOT NULL,
        coverage REAL NOT NULL,
        used_for_detection INTEGER NOT NULL
    )""")
    out.execute("CREATE TABLE dashboard_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        out.executemany("INSERT INTO detection_results VALUES (?,?,?,?,?,?,?,?)",
                        _detection_rows(conn))
        cov_rows, start = _coverage_rows(conn)
        out.executemany("INSERT INTO week_coverage VALUES (?,?,?,?,?)", cov_rows)
    finally:
        conn.close()

    meta = {
        "data_cutoff_date": DATA_CUTOFF_DATE,
        "coverage_start_week": start or "",
        "exclude_recent_weeks": str(EXCLUDE_RECENT_WEEKS),
        "min_week_extraction_coverage": str(MIN_WEEK_EXTRACTION_COVERAGE),
        "summary_model": SUMMARY_MODEL,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    out.executemany("INSERT INTO dashboard_meta VALUES (?, ?)", meta.items())


def run():
    os.makedirs(os.path.dirname(SNAPSHOT_PATH), exist_ok=True)
    tmp = SNAPSHOT_PATH + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)

    out = sqlite3.connect(tmp)
    out.execute("ATTACH DATABASE ? AS src", (f"file:{DB_PATH}?mode=ro",))

    # Copy each table's exact schema, then the rows the dashboard needs.
    for table in ("raw_incidents", "structured_incidents",
                  "weekly_category_counts", "pattern_summaries"):
        ddl = out.execute("SELECT sql FROM src.sqlite_master WHERE name = ?",
                          (table,)).fetchone()[0]
        out.execute(ddl)

    out.execute("""
        INSERT INTO raw_incidents
        SELECT id, created_at, title,
               CASE WHEN length(raw_text) > ? THEN substr(raw_text, 1, ?) || ?
                    ELSE raw_text END,
               source_url, labels_json, severity_label, root_cause_label, ingested_at
        FROM src.raw_incidents WHERE created_at >= ?
    """, (RAW_TEXT_CHARS, RAW_TEXT_CHARS, TRUNCATION_NOTE, DATA_CUTOFF_DATE))
    out.execute("""
        INSERT INTO structured_incidents SELECT s.* FROM src.structured_incidents s
        WHERE s.incident_id IN (SELECT id FROM raw_incidents)
    """)
    out.execute("INSERT INTO weekly_category_counts SELECT * FROM src.weekly_category_counts")
    out.execute("INSERT INTO pattern_summaries SELECT * FROM src.pattern_summaries WHERE active = 1")
    out.commit()
    out.execute("DETACH DATABASE src")

    _write_derived(out)
    out.execute("CREATE INDEX idx_raw_created ON raw_incidents(created_at)")
    out.commit()
    out.execute("VACUUM")
    out.close()
    os.replace(tmp, SNAPSHOT_PATH)  # atomic: a reader never sees a half-written file

    size_mb = os.path.getsize(SNAPSHOT_PATH) / 1e6
    print(f"Snapshot written: {os.path.normpath(SNAPSHOT_PATH)} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    run()
