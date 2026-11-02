"""
Reports current pipeline status at a glance: how much raw data
exists, how much is within the 3-year window, how much has been
extracted, model breakdown, and label coverage. Read-only, no
API calls - just queries the local database.

Usage:
    python -m src.check_status
"""

from src.db import get_connection
from src.config import DATA_CUTOFF_DATE


def run():
    conn = get_connection()

    total_raw = conn.execute(
        "SELECT COUNT(*) FROM raw_incidents"
    ).fetchone()[0]

    raw_in_window = conn.execute(
        "SELECT COUNT(*) FROM raw_incidents WHERE created_at >= ?",
        (DATA_CUTOFF_DATE,)
    ).fetchone()[0]

    total_structured = conn.execute(
        "SELECT COUNT(*) FROM structured_incidents"
    ).fetchone()[0]

    structured_in_window = conn.execute("""
        SELECT COUNT(*) FROM structured_incidents s
        JOIN raw_incidents r ON s.incident_id = r.id
        WHERE r.created_at >= ?
    """, (DATA_CUTOFF_DATE,)).fetchone()[0]

    remaining_in_window = raw_in_window - structured_in_window

    print("=" * 60)
    print("PIPELINE STATUS")
    print("=" * 60)
    print(f"Data window cutoff: {DATA_CUTOFF_DATE} onward\n")

    print(f"Raw incidents (all time):        {total_raw}")
    print(f"Raw incidents (in 3-yr window):   {raw_in_window}")
    print()
    print(f"Structured/extracted (all time):      {total_structured}")
    print(f"Structured/extracted (in window):     {structured_in_window}")
    print(f"Remaining to extract (in window):     {remaining_in_window}")
    print()

    # Model breakdown - which models produced how many records
    print("Extraction by model used:")
    model_counts = conn.execute("""
        SELECT llm_model, COUNT(*) FROM structured_incidents
        GROUP BY llm_model ORDER BY COUNT(*) DESC
    """).fetchall()
    for model, count in model_counts:
        print(f"  {model or '(unknown)'}: {count}")
    print()

    # Failure category distribution so far (within window)
    print("failure_category distribution (in window, so far):")
    cat_counts = conn.execute("""
        SELECT s.failure_category, COUNT(*) FROM structured_incidents s
        JOIN raw_incidents r ON s.incident_id = r.id
        WHERE r.created_at >= ?
        GROUP BY s.failure_category ORDER BY COUNT(*) DESC
    """, (DATA_CUTOFF_DATE,)).fetchall()
    for cat, count in cat_counts:
        print(f"  {cat}: {count}")
    print()

    # Label ground-truth coverage, for context on Phase 3 validation potential
    severity_gt = conn.execute("""
        SELECT COUNT(*) FROM structured_incidents s
        JOIN raw_incidents r ON s.incident_id = r.id
        WHERE r.created_at >= ? AND r.severity_label IS NOT NULL
    """, (DATA_CUTOFF_DATE,)).fetchone()[0]

    rootcause_gt = conn.execute("""
        SELECT COUNT(*) FROM structured_incidents s
        JOIN raw_incidents r ON s.incident_id = r.id
        WHERE r.created_at >= ? AND r.root_cause_label IS NOT NULL
    """, (DATA_CUTOFF_DATE,)).fetchone()[0]

    print(f"Extracted records WITH existing severity ground truth:    {severity_gt} / {structured_in_window}")
    print(f"Extracted records WITH existing root-cause ground truth:  {rootcause_gt} / {structured_in_window}")
    print("=" * 60)

    conn.close()


if __name__ == "__main__":
    run()