"""
Phase 5 - Pattern detection.

Reads weekly_category_counts (built by aggregate.py) and, per
failure_category, checks whether the recent trend is a real,
notable shift - not just normal week-to-week noise.

Both detectors run on VOLUME-ADJUSTED counts, not raw counts: a
category's expected count in a week is that week's total incidents times
the category's overall share, and each week is scored by the Pearson
residual (count - expected) / sqrt(expected). Raw counts rise and fall
with total incident volume, so one busy month showed up as the same
"trend" in half the categories at once (see PROJECT_LOG.md, 2026-11-10).

Two techniques, applied per category:
  1. Unusual weeks - is a single week far above/below what the week's
     total volume predicts for this category?
  2. Change-point detection (ruptures) - did the category's share of
     incidents shift at some point, not just one noisy week?

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
import numpy as np

from src.config import (EXCLUDE_RECENT_WEEKS, MIN_WEEKS_FOR_DETECTION, MIN_TOTAL_FOR_ZSCORE,
                        MIN_WEEK_EXTRACTION_COVERAGE, DATA_CUTOFF_DATE,
                        ANOMALY_RESIDUAL_THRESHOLD, CHANGEPOINT_PENALTY_FACTOR)


def weekly_coverage(conn):
    """
    Per-week extraction coverage within the data window: a DataFrame
    indexed by week_start (Monday, same bucketing as aggregate.py) with
    raw / extracted counts and coverage fraction.
    """
    df = pd.read_sql_query("""
        SELECT r.created_at, s.incident_id IS NOT NULL AS extracted
        FROM raw_incidents r
        LEFT JOIN structured_incidents s ON s.incident_id = r.id
        WHERE r.created_at >= ?
    """, conn, params=(DATA_CUTOFF_DATE,))
    if df.empty:
        return pd.DataFrame(columns=["raw", "extracted", "coverage"])
    df["created_at"] = pd.to_datetime(df["created_at"]).dt.tz_localize(None)
    df["week_start"] = df["created_at"].dt.to_period("W").dt.start_time
    out = df.groupby("week_start")["extracted"].agg(raw="size", extracted="sum")
    out["coverage"] = out["extracted"] / out["raw"]
    return out.sort_index()


def coverage_start_week(conn):
    """
    Returns the first week (ISO date string) of the most recent unbroken
    run of weeks whose extraction coverage is >= MIN_WEEK_EXTRACTION_COVERAGE,
    or None if no week qualifies. Weeks before this are only partly
    extracted, so their counts are artificially low (left-censoring - the
    mirror image of the right-censoring handled by EXCLUDE_RECENT_WEEKS).
    """
    cov = weekly_coverage(conn)
    if cov.empty:
        return None
    coverage = cov["coverage"]

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


def build_total_series(df):
    """Total incidents per week across all categories, gap-filled like
    build_full_series so the two align week for week."""
    totals = df.groupby("week_start")["count"].sum()
    totals.index = pd.to_datetime(totals.index)
    full_range = pd.date_range(
        start=df["week_start"].min(), end=df["week_start"].max(), freq="W-MON")
    return totals.reindex(full_range, fill_value=0)


def volume_residuals(counts, totals):
    """
    Expected count per week if the category kept its overall share of
    that week's incidents, and the Pearson residual against it. Weeks with
    no incidents at all have nothing to compare, so their residual is 0.
    """
    share = counts.sum() / totals.sum()
    expected = totals * share
    resid = ((counts - expected) / np.sqrt(expected.where(expected > 0))).fillna(0.0)
    return expected, resid


def unusual_weeks(counts, expected, resid, threshold=ANOMALY_RESIDUAL_THRESHOLD):
    return [(idx.date(), int(c), round(float(r), 2), round(float(e), 2))
            for idx, c, e, r in zip(counts.index, counts.values,
                                    expected.values, resid.values)
            if abs(r) >= threshold]


def changepoints(resid, min_size=3):
    if len(resid) < min_size * 2:
        return []
    pen = CHANGEPOINT_PENALTY_FACTOR * np.log(len(resid))
    algo = rpt.Pelt(model="l2", min_size=min_size).fit(resid.values.reshape(-1, 1))
    breakpoints = [b for b in algo.predict(pen=pen) if b < len(resid)]  # drop end-marker
    return [resid.index[b].date() for b in breakpoints]


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
    totals = apply_censoring_exclusion(build_total_series(df))
    expected, resid = volume_residuals(usable, totals)
    result = {
        "category": category,
        "usable": usable,
        "usable_weeks": len(usable),
        "total": int(usable.sum()),
        "week_totals": totals,
        "expected": expected,
        "residuals": resid,
        "anomalies": [],     # (week, count, residual, expected)
        "changepoints": [],
    }

    if len(usable) < MIN_WEEKS_FOR_DETECTION:
        result["status"] = "insufficient_weeks"
    elif usable.sum() < MIN_TOTAL_FOR_ZSCORE:
        result["status"] = "too_sparse"
    else:
        result["status"] = "evaluated"
        result["anomalies"] = unusual_weeks(usable, expected, resid)
        result["changepoints"] = changepoints(resid)
    return result


TOTAL_LABEL = "All incidents"


def evaluate_total(df):
    """
    Detection on TOTAL weekly incident volume - the signal the
    volume-adjusted category detection deliberately removes. Without this,
    a surge across all categories (e.g. 49 -> 89/week in Apr-May 2026)
    would be flagged nowhere.

    Noise scale is estimated robustly from week-to-week differences (MAD),
    so a few extreme weeks don't inflate it; on this data it matches the
    Poisson sqrt(mean). The same threshold and penalty as the category
    detectors then apply to the standardized series.
    """
    totals = apply_censoring_exclusion(build_total_series(df))
    result = {"category": TOTAL_LABEL, "usable": totals, "usable_weeks": len(totals),
              "total": int(totals.sum()), "anomalies": [], "changepoints": []}
    if len(totals) < MIN_WEEKS_FOR_DETECTION:
        result["status"] = "insufficient_weeks"
        return result
    d = totals.diff().dropna()
    sigma = float(np.median(np.abs(d - np.median(d))) / 0.6745 / np.sqrt(2))
    if sigma == 0:
        sigma = float(d.std() / np.sqrt(2)) or 1.0
    baseline = float(totals.median())
    z = (totals - baseline) / sigma
    result["status"] = "evaluated"
    result["baseline"] = baseline
    result["anomalies"] = [(idx.date(), int(c), round(float(zz), 2), round(baseline, 1))
                           for idx, c, zz in zip(totals.index, totals.values, z.values)
                           if abs(zz) >= ANOMALY_RESIDUAL_THRESHOLD]
    result["changepoints"] = changepoints(totals / sigma)
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

    t = evaluate_total(df)
    if t["status"] == "evaluated":
        print(f"[{TOTAL_LABEL}] {t['usable_weeks']} usable weeks, typical week "
              f"{t['baseline']:.0f} incidents")
        for week, count, z, base in t["anomalies"]:
            print(f"    UNUSUAL WEEK: week of {week} had {count} incidents "
                  f"(typical {base:.0f}, z={z})")
        for cp in t["changepoints"]:
            print(f"    CHANGE-POINT in total volume around week of {cp}")
        print()

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

        for week, count, resid, exp in r["anomalies"]:
            print(f"    UNUSUAL WEEK: week of {week} had {count} incidents, "
                  f"{exp} expected from that week's total volume (residual {resid})")

        for cp in r["changepoints"]:
            print(f"    CHANGE-POINT in share of incidents around week of {cp}")

        if not r["anomalies"] and not r["changepoints"]:
            print(f"    No notable pattern detected.")
        print()


if __name__ == "__main__":
    run()