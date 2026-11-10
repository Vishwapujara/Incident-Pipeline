"""
Phase 2 - LLM extraction.

Finds every row in raw_incidents that does NOT yet have a matching
row in structured_incidents, runs LLM extraction on its raw_text,
and writes the validated result. Safe to re-run at any time - it
only ever processes what's missing, so an interrupted run just
picks up where it left off (this is the same idempotency principle
Phase 9's continuous polling will depend on).

Usage:
    python -m src.extract                  # process everything unprocessed
    python -m src.extract --limit 50        # process only the next 50 (testing)
"""

import sys
from datetime import datetime, timezone

from src.db import get_connection, init_schema
from src.llm_client import extract_incident, DailyRateLimitExceeded, ProviderUnavailable
from src.config import LLM_MODEL_CHAIN, DATA_CUTOFF_DATE


def get_unprocessed(conn, limit=None):
    query = """
        SELECT r.id, r.raw_text
        FROM raw_incidents r
        LEFT JOIN structured_incidents s ON r.id = s.incident_id
        WHERE s.incident_id IS NULL
          AND r.raw_text IS NOT NULL
          AND length(r.raw_text) > 0
          AND r.created_at >= ?
        ORDER BY r.created_at DESC
    """
    params = [DATA_CUTOFF_DATE]
    if limit:
        query += " LIMIT ?"
        params.append(int(limit))
    return conn.execute(query, params).fetchall()


def save_result(conn, incident_id, result, model_used):
    conn.execute("""
        INSERT OR REPLACE INTO structured_incidents
            (incident_id, failure_category, severity_assessment,
             root_cause_summary, affected_service, llm_model, extracted_at,
             root_cause_identified)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        incident_id,
        result.failure_category,
        result.severity_assessment,
        result.root_cause_summary,
        result.affected_service,
        model_used,
        datetime.now(timezone.utc).isoformat(),
        None if result.root_cause_identified is None else int(result.root_cause_identified),
    ))


def run(limit=None):
    init_schema()
    conn = get_connection()

    rows = get_unprocessed(conn, limit=limit)
    total = len(rows)
    print(f"Scoped to incidents from {DATA_CUTOFF_DATE} onward (most recent first).")
    print(f"Found {total} incidents needing extraction.")

    if total == 0:
        print("Nothing to do - all incidents already processed.")
        conn.close()
        return

    model_index = 0
    current_model = LLM_MODEL_CHAIN[model_index]
    print(f"Using model: {current_model}")

    succeeded, failed = 0, 0
    stopped_early = False

    for i, (incident_id, raw_text) in enumerate(rows, start=1):
        # Inner loop: retry the SAME record if we need to switch models
        # mid-way (rate limit hit), rather than skipping the record.
        while True:
            try:
                result = extract_incident(raw_text, model=current_model)
                break  # got a real result (or a clean None), move on
            except ProviderUnavailable as e:
                print(f"\nCan't reach the LLM API at record {i}/{total} "
                      f"(id={incident_id}) after backing off: {e}")
                print("Stopping run - progress is saved. Re-run when the "
                      "connection is back; finished records are skipped.")
                stopped_early = True
                break
            except DailyRateLimitExceeded as e:
                conn.commit()  # save everything successful so far
                print(f"\nRate limit hit on '{current_model}' at record "
                      f"{i}/{total} (id={incident_id}).")

                model_index += 1
                if model_index >= len(LLM_MODEL_CHAIN):
                    print("All models in the fallback chain are rate-limited. "
                          "Stopping run - progress is saved.")
                    print(f"Provider message: {e}")
                    print("Re-run 'python -m src.extract' later to continue "
                          "(already-processed records are skipped automatically).")
                    stopped_early = True
                    break

                current_model = LLM_MODEL_CHAIN[model_index]
                print(f"Switching to backup model: {current_model}")
                # loop again - retries THIS SAME record with the new model

        if stopped_early:
            break

        if result is None:
            failed += 1
            print(f"[{i}/{total}] id={incident_id} SKIPPED (extraction failed)")
            continue

        save_result(conn, incident_id, result, current_model)
        # Commit every record: an open write transaction locks the whole
        # SQLite file, which blocked aggregate/summarize from running
        # alongside a batch. One small commit per LLM call is negligible.
        conn.commit()
        succeeded += 1

        if i % 25 == 0:
            print(f"[{i}/{total}] processed so far ({succeeded} ok, {failed} failed, "
                  f"current model: {current_model})")

    conn.commit()
    conn.close()
    if stopped_early:
        print(f"\nStopped early. Succeeded: {succeeded}, Failed: {failed}, "
              f"Attempted before stopping: {succeeded + failed}")
    else:
        print(f"\nDone. Succeeded: {succeeded}, Failed: {failed}, Total attempted: {total}")


if __name__ == "__main__":
    limit_arg = None
    if "--limit" in sys.argv:
        idx = sys.argv.index("--limit")
        limit_arg = int(sys.argv[idx + 1])
    run(limit=limit_arg)