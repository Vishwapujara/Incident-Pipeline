"""
Exports a small, read-only SQLite copy of the database for the dashboard
when it isn't connected to Postgres (local dev without Docker, and the
Vercel deployment, which downloads it from the dashboard-data branch).

The working database (data/processed/incidents.db, ~90 MB) is not in git.
This writes snapshot/dashboard.db with only what the dashboard reads:
  - incidents inside the data window, with report text cut to
    RAW_TEXT_CHARS (the full text is one click away on GitLab)
  - the extracted fields, weekly counts and active pattern summaries
  - the dashboard tables (src.dashboard_tables: precomputed detection
    output, coverage and config), refreshed first

SQLite backend only - in Postgres mode the web app reads the database
directly and no snapshot is needed.

Usage:
    python -m src.export_snapshot
"""

import os
import sqlite3

from src import dashboard_tables
from src.config import DATA_CUTOFF_DATE, DB_PATH
from src.db import BACKEND

SNAPSHOT_PATH = os.path.join(os.path.dirname(__file__), "..", "snapshot", "dashboard.db")
RAW_TEXT_CHARS = 3000
TRUNCATION_NOTE = "\n\n[... truncated - open the issue on GitLab for the full report]"
COPIED_WHOLE = ("weekly_category_counts", "detection_results", "week_coverage", "dashboard_meta")


def run():
    if BACKEND != "sqlite":
        raise SystemExit("export_snapshot reads the SQLite working DB; unset DATABASE_URL. "
                         "(With Postgres the dashboard reads the database directly.)")
    dashboard_tables.run()

    os.makedirs(os.path.dirname(SNAPSHOT_PATH), exist_ok=True)
    tmp = SNAPSHOT_PATH + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)

    out = sqlite3.connect(tmp)
    out.execute("ATTACH DATABASE ? AS src", (f"file:{DB_PATH}?mode=ro",))

    # Copy each table's exact schema, then the rows the dashboard needs.
    for table in ("raw_incidents", "structured_incidents", "pattern_summaries") + COPIED_WHOLE:
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
    out.execute("INSERT INTO pattern_summaries SELECT * FROM src.pattern_summaries WHERE active = 1")
    for table in COPIED_WHOLE:
        out.execute(f"INSERT INTO {table} SELECT * FROM src.{table}")
    out.execute("CREATE INDEX idx_raw_created ON raw_incidents(created_at)")
    out.commit()
    out.execute("DETACH DATABASE src")
    out.execute("VACUUM")
    out.close()
    os.replace(tmp, SNAPSHOT_PATH)  # atomic: a reader never sees a half-written file

    size_mb = os.path.getsize(SNAPSHOT_PATH) / 1e6
    print(f"Snapshot written: {os.path.normpath(SNAPSHOT_PATH)} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    run()
