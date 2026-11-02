"""
Phase 4 - Aggregation + time series.

Turns individual structured incidents into an actual time series:
count of incidents per failure_category, per week. The RAW incidents
are individual events; this table is the derived series - one row
per (week, category) pair, with a count - that Phase 5's pattern
detection will actually operate on.

Fully recomputes weekly_category_counts every run (not incremental)
- unlike extraction, this is cheap (a pandas groupby over whatever
currently exists in structured_incidents), so there's no benefit to
incremental logic here. Safe and correct to re-run after every new
extraction batch.

Usage:
    python -m src.aggregate
"""

import pandas as pd

from src.db import get_connection, init_schema


def run():
    init_schema()
    conn = get_connection()

    df = pd.read_sql_query("""
        SELECT s.failure_category, r.created_at
        FROM structured_incidents s
        JOIN raw_incidents r ON s.incident_id = r.id
    """, conn)

    if df.empty:
        print("No structured incidents yet - nothing to aggregate.")
        conn.close()
        return

    df["created_at"] = pd.to_datetime(df["created_at"]).dt.tz_localize(None)

    # Bucket into weeks - week_start is the Monday of that week,
    # this is the "week" dimension every later phase (trend
    # detection, dashboard) will key off of.
    df["week_start"] = (
        df["created_at"].dt.to_period("W").dt.start_time.dt.strftime("%Y-%m-%d")
    )

    weekly_counts = (
        df.groupby(["week_start", "failure_category"])
        .size()
        .reset_index(name="count")
    )

    # Full recompute: clear and rewrite, rather than trying to
    # incrementally patch - cheap enough that correctness > cleverness.
    conn.execute("DELETE FROM weekly_category_counts")
    weekly_counts.to_sql(
        "weekly_category_counts", conn, if_exists="append", index=False
    )
    conn.commit()

    print(f"Aggregated {len(df)} incidents into "
          f"{len(weekly_counts)} (week, category) rows.")
    print(f"Date range: {df['created_at'].min().date()} to "
          f"{df['created_at'].max().date()}")
    print(f"Categories: {sorted(df['failure_category'].unique())}")

    conn.close()


if __name__ == "__main__":
    run()