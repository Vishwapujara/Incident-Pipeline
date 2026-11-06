"""
Phase 1 - Raw ingestion.

Reads the already-pulled data/raw/issues_raw.json (from
explore_incident_dataset.py) and loads it into a real,
queryable raw_incidents table in SQLite.

Idempotent: running this multiple times will not create
duplicate rows - it uses INSERT OR REPLACE keyed on the
issue's real GitLab id, so re-running is always safe.

Usage:
    python -m src.ingest
"""

import json
from datetime import datetime, timezone

from src.config import RAW_DATA_PATH
from src.db import get_connection, init_schema
from src.label_utils import extract_severity, extract_root_cause


def load_raw_issues():
    with open(RAW_DATA_PATH) as f:
        return json.load(f)


def upsert_issues(conn, issues):
    """
    Inserts new issues and updates existing ones in place. Shared by the
    one-off cache load (run) and Phase 9's incremental poll.

    Uses ON CONFLICT ... DO UPDATE rather than INSERT OR REPLACE so that
    ingested_at keeps meaning "when this pipeline FIRST saw the record"
    (REPLACE deletes and re-inserts the row, resetting it every run).
    Returns (new_count, updated_count).
    """
    now = datetime.now(timezone.utc).isoformat()
    existing = {r[0] for r in conn.execute("SELECT id FROM raw_incidents")}
    new = 0
    for issue in issues:
        labels = issue.get("labels", [])
        conn.execute("""
            INSERT INTO raw_incidents
                (id, created_at, title, raw_text, source_url,
                 labels_json, severity_label, root_cause_label, ingested_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                created_at = excluded.created_at,
                title = excluded.title,
                raw_text = excluded.raw_text,
                source_url = excluded.source_url,
                labels_json = excluded.labels_json,
                severity_label = excluded.severity_label,
                root_cause_label = excluded.root_cause_label
        """, (
            issue["iid"],
            issue["created_at"],
            issue.get("title"),
            issue.get("description"),
            issue.get("web_url"),
            json.dumps(labels),
            extract_severity(labels),
            extract_root_cause(labels),
            now,
        ))
        if issue["iid"] not in existing:
            new += 1
    conn.commit()
    return new, len(issues) - new


def run():
    init_schema()
    issues = load_raw_issues()
    print(f"Loaded {len(issues)} issues from local cache to ingest.")

    conn = get_connection()
    new, updated = upsert_issues(conn, issues)

    # Sanity check: confirm row count matches what we tried to insert
    count = conn.execute("SELECT COUNT(*) FROM raw_incidents").fetchone()[0]
    conn.close()

    print(f"Inserted {new} new rows, updated {updated} existing rows.")
    print(f"Total rows now in raw_incidents table: {count}")


if __name__ == "__main__":
    run()