"""
Phase 5 - Pattern detection.

Reads weekly_category_counts (built by aggregate.py) and, per
failure_category, checks whether the recent trend is a real,
notable shift - not just normal week-to-week noise.

Two techniques, applied per category:
  1. Z-score anomaly flagging - is any single week unusually high
     compared to that category's own history?
  2. Change-point detection (ruptures) - did the underlying RATE
     shift at some point, not just one noisy week?

Critical correctness requirement (see PROJECT_LOG.md - "discovered
right-censoring in recent weeks"): the most recent EXCLUDE_RECENT_WEEKS
weeks are dropped before any detection runs. raw_incidents only
contains closed issues, so the newest weeks are always artificially
incomplete (incidents from last week may still be open) - running
detection on them would produce a FALSE "sudden drop" every time,
not a real finding.

Categories with fewer than MIN_WEEKS_FOR_DETECTION usable weeks are
skipped with an explicit "not enough data yet" message, rather than
forcing a statistically meaningless result.

Usage:
    python -m src.detect_patterns
"""

import pandas as pd
import ruptures as rpt

from src.db import get_connection
from src.config import (EXCLUDE_RECENT_WEEKS, MIN_WEEKS_FOR_DETECTION, MIN_TOTAL_FOR_ZSCORE,
                        MIN_WEEK_EXTRACTION_COVERAGE, DATA_CUTOFF_DATE)


def coverage_start_week(conn):
    """
    Returns the first week (ISO date string) of the most recent unbroken
    run of weeks whose extraction coverage is >= MIN_WEEK_EXTRACTION_COVERAGE,
    or None if no week qualifies. Weeks before this are only partly
    extracted, so their counts are artificially low (left-censoring - the
    mirror image of the right-censoring handled by EXCLUDE_RECENT_WEEKS).
    """
    df = pd.read_sql_query("""
        SELECT r.created_at, s.incident_id IS NOT NULL AS extracted
        FROM raw_incidents r
        LEFT JOIN structured_incidents s ON s.incident_id = r.id
        WHERE r.created_at >= ?
    """, conn, params=(DATA_CUTOFF_DATE,))
    if df.empty:
        return None
    df["created_at"] = pd.to_datetime(df["created_at"]).dt.tz_localize(None)
    # Same week bucketing as aggregate.py (Monday week starts).
    df["week_start"] = df["created_at"].dt.to_period("W").dt.start_time
    coverage = df.groupby("week_start")["extracted"].mean().sort_index()

    start = None
    for week, frac in coverage[::-1].items():  # newest -> oldest
        if frac < MIN_WEEK_EXTRACTION_COVERAGE:
            break
        start = week
    return start.strftime("%Y-%m-%d") if start is not None else None


def load_series(conn):
    df = pd.read_sql_query(
        "SELECT week_start, failure_category, count FROM weekly_category_counts",
        conn
    )
    start = coverage_start_week(conn)
    if start is None:
        return df.iloc[0:0]
    return df[df["week_start"] >= start].reset_index(drop=True)


def build_full_series(df, category):
    """
    Pivots one category into a complete, gap-filled weekly series -
    weeks with zero incidents for this category need an explicit 0,
    not a missing row, or the series would look shorter/denser than
    it really is.
    """
    cat_df = df[df["failure_category"] == category].copy()
    cat_df["week_start"] = pd.to_datetime(cat_df["week_start"])
    cat_df = cat_df.set_index("week_start").sort_index()

    full_range = pd.date_range(
        start=df["week_start"].min(), end=df["week_start"].max(), freq="W-MON"
    )
    series = cat_df["count"].reindex(full_range, fill_value=0)
    return series


def apply_censoring_exclusion(series):
    """Drops the most recent EXCLUDE_RECENT_WEEKS - see module docstring."""
    if len(series) <= EXCLUDE_RECENT_WEEKS:
        return series.iloc[0:0]  # nothing usable left
    return series.iloc[:-EXCLUDE_RECENT_WEEKS]


def zscore_anomalies(series, threshold=2.0):
    mean, std = series.mean(), series.std()
    if std == 0 or pd.isna(std):
        return []
    z = (series - mean) / std
    return [(idx.date(), val, round(z_val, 2))
            for idx, val, z_val in zip(series.index, series.values, z)
            if abs(z_val) >= threshold]


def changepoints(series, min_size=3):
    if len(series) < min_size * 2:
        return []
    algo = rpt.Pelt(model="l2", min_size=min_size).fit(series.values.reshape(-1, 1))
    breakpoints = algo.predict(pen=3)
    breakpoints = [b for b in breakpoints if b < len(series)]  # drop trailing end-marker
    return [series.index[b].date() for b in breakpoints]


def evaluate_category(df, category):
    """
    Runs all gates + detectors for one category and returns the result
    as data, so Phase 7 (summarize.py) can consume the same findings
    run() prints - one place owns the gating logic, not two.

    status is one of:
      "insufficient_weeks" - fewer than MIN_WEEKS_FOR_DETECTION usable weeks
      "too_sparse"         - total below MIN_TOTAL_FOR_ZSCORE
      "evaluated"          - detectors ran (anomalies/changepoints may be empty)
    """
    full_series = build_full_series(df, category)
    usable = apply_censoring_exclusion(full_series)
    result = {
        "category": category,
        "usable": usable,
        "usable_weeks": len(usable),
        "total": int(usable.sum()),
        "anomalies": [],
        "changepoints": [],
    }

    if len(usable) < MIN_WEEKS_FOR_DETECTION:
        result["status"] = "insufficient_weeks"
    elif usable.sum() < MIN_TOTAL_FOR_ZSCORE:
        result["status"] = "too_sparse"
    else:
        result["status"] = "evaluated"
        result["anomalies"] = zscore_anomalies(usable)
        result["changepoints"] = changepoints(usable)
    return result


def run():
    conn = get_connection()
    df = load_series(conn)
    conn.close()

    if df.empty:
        print("No aggregated data yet - run aggregate.py first.")
        return

    print(f"Using weeks from {df['week_start'].min()} on (earlier weeks are "
          f"<{MIN_WEEK_EXTRACTION_COVERAGE:.0%} extracted - left-censoring correction).")
    categories = sorted(df["failure_category"].unique())
    print(f"Checking {len(categories)} categories "
          f"(excluding the most recent {EXCLUDE_RECENT_WEEKS} weeks "
          f"from all of them - right-censoring correction)...\n")

    for category in categories:
        r = evaluate_category(df, category)

        if r["status"] == "insufficient_weeks":
            print(f"[{category}] Only {r['usable_weeks']} usable weeks "
                  f"(need {MIN_WEEKS_FOR_DETECTION}+) - skipping, not enough data yet.")
            continue

        print(f"[{category}] {r['usable_weeks']} usable weeks, "
              f"total incidents: {r['total']}")

        if r["status"] == "too_sparse":
            print(f"    Too sparse to evaluate (total < {MIN_TOTAL_FOR_ZSCORE}) - "
                  f"skipping anomaly/change-point checks, not enough volume "
                  f"for the statistics to mean anything.\n")
            continue

        for week, count, z in r["anomalies"]:
            print(f"    ANOMALY: week of {week} had {count} incidents (z={z})")

        for cp in r["changepoints"]:
            print(f"    CHANGE-POINT detected around week of {cp}")

        if not r["anomalies"] and not r["changepoints"]:
            print(f"    No notable pattern detected.")
        print()


if __name__ == "__main__":
    run()