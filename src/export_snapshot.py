"""
Exports a small, read-only copy of the database for the hosted dashboard.

The working database (data/processed/incidents.db, ~90 MB) is not in git.
The hosted dashboard (Streamlit Community Cloud) needs its own copy, so
this writes snapshot/dashboard.db with only what the dashboard reads:
  - incidents inside the data window, with report text cut to
    RAW_TEXT_CHARS (the full text is one click away on GitLab)
  - the extracted fields, weekly counts and active pattern summaries

The daily job (scripts/daily.sh) runs this after poll and pushes the
file, and Streamlit Cloud redeploys on push.

Usage:
    python -m src.export_snapshot
"""

import os
import sqlite3

from src.config import DATA_CUTOFF_DATE, DB_PATH

SNAPSHOT_PATH = os.path.join(os.path.dirname(__file__), "..", "snapshot", "dashboard.db")
RAW_TEXT_CHARS = 3000
TRUNCATION_NOTE = "\n\n[... truncated - open the issue on GitLab for the full report]"


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
    out.execute("VACUUM")
    out.close()
    os.replace(tmp, SNAPSHOT_PATH)  # atomic: a reader never sees a half-written file

    size_mb = os.path.getsize(SNAPSHOT_PATH) / 1e6
    print(f"Snapshot written: {os.path.normpath(SNAPSHOT_PATH)} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    run()
