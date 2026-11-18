"""
Writes the tables the web dashboard reads on top of the pipeline's own:

  detection_results  Phase 5 output per series (each category + total volume)
  week_coverage      per-week extraction coverage and whether detection used it
  dashboard_meta     the config values the UI explains (window, thresholds, ...)

Computed here, in Python, so the statistics (ruptures/PELT, residuals,
censoring rules) live in one language and the TypeScript app only reads.
Runs at the end of every poll cycle and before every snapshot export.
Works on either backend (src.db).

Usage:
    python -m src.dashboard_tables
"""

import json
from datetime import datetime, timezone

from src.config import (DATA_CUTOFF_DATE, EXCLUDE_RECENT_WEEKS,
                        MIN_WEEK_EXTRACTION_COVERAGE, SUMMARY_MODEL)
from src.db import get_connection, init_schema
from src.detect_patterns import (load_series, evaluate_category, evaluate_total,
                                 weekly_coverage, coverage_start_week)

TOTAL_KEY = "__total__"


def detection_rows(conn):
    analysed = load_series(conn)  # coverage-filtered: what detection uses
    if analysed.empty:
        return []
    results = [(c, evaluate_category(analysed, c))
               for c in sorted(analysed["failure_category"].unique())]
    results.append((TOTAL_KEY, evaluate_total(analysed)))
    return [(
        key, r["status"], int(r["total"]), int(r["usable_weeks"]),
        None if r.get("baseline") is None else float(r["baseline"]),
        json.dumps([str(d.date()) for d in r["usable"].index]),
        json.dumps([{"week": str(w), "count": int(c), "score": float(s), "expected": float(e)}
                    for w, c, s, e in r["anomalies"]]),
        json.dumps([str(d) for d in r["changepoints"]]),
    ) for key, r in results]


def coverage_rows(conn):
    cov = weekly_coverage(conn)
    if cov.empty:
        return [], None
    start = coverage_start_week(conn)
    # Same rule as detection: from the coverage start on, minus the newest
    # EXCLUDE_RECENT_WEEKS (right-censoring).
    recent = set(list(cov.index)[-EXCLUDE_RECENT_WEEKS:])
    rows = []
    for week, r in cov.iterrows():
        day = week.strftime("%Y-%m-%d")
        used = start is not None and day >= start and week not in recent
        rows.append((day, int(r["raw"]), int(r["extracted"]), float(r["coverage"]), int(used)))
    return rows, start


def run():
    init_schema()
    conn = get_connection()
    det = detection_rows(conn)
    cov, start = coverage_rows(conn)
    meta = {
        "data_cutoff_date": DATA_CUTOFF_DATE,
        "coverage_start_week": start or "",
        "exclude_recent_weeks": str(EXCLUDE_RECENT_WEEKS),
        "min_week_extraction_coverage": str(MIN_WEEK_EXTRACTION_COVERAGE),
        "summary_model": SUMMARY_MODEL,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    # Replace all three in one transaction: a reader sees the old set or
    # the new set, never a mix.
    for table in ("detection_results", "week_coverage", "dashboard_meta"):
        conn.execute(f"DELETE FROM {table}")
    conn.executemany("INSERT INTO detection_results VALUES (?, ?, ?, ?, ?, ?, ?, ?)", det)
    conn.executemany("INSERT INTO week_coverage VALUES (?, ?, ?, ?, ?)", cov)
    conn.executemany("INSERT INTO dashboard_meta VALUES (?, ?)", list(meta.items()))
    conn.commit()
    conn.close()
    print(f"Dashboard tables: {len(det)} series, {len(cov)} weeks of coverage, "
          f"detection from {start or '—'}")


if __name__ == "__main__":
    run()
