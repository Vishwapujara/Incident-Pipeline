"""
Phase 9 - Continuous ingestion.

One run = one full refresh cycle, designed to be scheduled (cron /
launchd, or Prefect in Phase 10):

  1. Pull closed issues UPDATED since the last poll from GitLab
     (a few API pages, not the full 131-page history) and upsert them.
     This picks up new incidents, older incidents that were still open
     at the previous pull, and label edits.
  2. If an already-extracted incident's report text changed (postmortems
     are often filled in after the issue closes), drop its extraction so
     it gets re-extracted from the new text.
  3. Extract a bounded batch (newest first, idempotent - Phase 2).
  4. Re-aggregate, re-detect and re-summarize (Phases 4-7; Phase 7 only
     spends tokens on new/changed findings).

The dashboard reads the database fresh on each load, so it reflects the
cycle automatically.

Usage:
    python -m src.poll                       # full cycle
    python -m src.poll --extract-limit 100   # smaller extraction batch
    python -m src.poll --no-extract --no-summarize   # GitLab pull only
"""

import argparse
from datetime import datetime, timedelta, timezone

from src.db import get_connection, init_schema
from src.gitlab_client import fetch_closed_issues_updated_since
from src.ingest import upsert_issues

STATE_KEY = "last_poll_started_at"
# Overlap each pull with the previous one so an issue updated while the
# last poll was running can't fall between the two windows. Upserts are
# idempotent, so re-fetching a few issues costs nothing.
OVERLAP = timedelta(hours=1)


def _get_since(conn):
    row = conn.execute(
        "SELECT value FROM pipeline_state WHERE key = ?", (STATE_KEY,)).fetchone()
    if row:
        return datetime.fromisoformat(row[0]) - OVERLAP
    # First poll ever: start from the original bulk pull (Phase 0/1).
    first = conn.execute("SELECT MIN(ingested_at) FROM raw_incidents").fetchone()[0]
    if first is None:
        raise RuntimeError("raw_incidents is empty - run the Phase 0/1 bulk "
                           "pull (explore_incident_dataset --full, ingest) first.")
    return datetime.fromisoformat(first) - OVERLAP


def pull_from_gitlab():
    init_schema()
    conn = get_connection()
    started = datetime.now(timezone.utc)
    since = _get_since(conn)
    print(f"Fetching closed issues updated since {since:%Y-%m-%d %H:%M} UTC...")

    issues = fetch_closed_issues_updated_since(since.isoformat())

    # Find already-extracted incidents whose report text is about to change.
    ids = [i["iid"] for i in issues]
    changed = []
    if ids:
        old_text = {}
        for chunk in range(0, len(ids), 500):
            part = ids[chunk:chunk + 500]
            q = ",".join("?" * len(part))
            old_text.update(conn.execute(f"""
                SELECT r.id, r.raw_text FROM raw_incidents r
                JOIN structured_incidents s ON s.incident_id = r.id
                WHERE r.id IN ({q})
            """, part).fetchall())
        changed = [i["iid"] for i in issues
                   if i["iid"] in old_text
                   and (i.get("description") or "") != (old_text[i["iid"]] or "")]

    new, updated = upsert_issues(conn, issues)

    if changed:
        q = ",".join("?" * len(changed))
        conn.execute(f"DELETE FROM structured_incidents WHERE incident_id IN ({q})", changed)

    # Only advance the cursor once the pull and upsert have succeeded.
    conn.execute("INSERT OR REPLACE INTO pipeline_state (key, value) VALUES (?, ?)",
                 (STATE_KEY, started.isoformat()))
    conn.commit()
    conn.close()

    print(f"GitLab: {len(issues)} issues fetched - {new} new, {updated} updated; "
          f"{len(changed)} extracted incidents had changed report text and "
          f"will be re-extracted.")
    return new, updated, len(changed)


def run(extract_limit=300, do_extract=True, do_summarize=True):
    pull_from_gitlab()

    if do_extract:
        print("\n=== Phase 2: extraction ===")
        from src import extract
        extract.run(limit=extract_limit)

    if not do_summarize:  # summarize.run() re-aggregates itself
        print("\n=== Phase 4: aggregation ===")
        from src import aggregate
        aggregate.run()

    if do_summarize:
        print("\n=== Phases 5-7: detection, clustering, summaries ===")
        from src import summarize
        summarize.run()

    print("\nCycle complete - the dashboard will show the new data on next load.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 9 - one refresh cycle")
    parser.add_argument("--extract-limit", type=int, default=300)
    parser.add_argument("--no-extract", action="store_true")
    parser.add_argument("--no-summarize", action="store_true")
    args = parser.parse_args()
    run(extract_limit=args.extract_limit,
        do_extract=not args.no_extract,
        do_summarize=not args.no_summarize)
