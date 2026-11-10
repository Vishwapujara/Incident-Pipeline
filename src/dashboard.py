"""
Phase 8 - Dashboard.

Read-only Streamlit view over everything the pipeline produces:
  - Patterns: Phase 7's explanations of Phase 5/6 findings, with the
    model's "is this a real pattern?" verdict and the cited incidents.
  - Trends: weekly counts per category, showing exactly which weeks the
    detection used (partly-extracted and too-recent weeks are shaded),
    with change-points and anomalies marked.
  - Search: every incident in the data window (extracted or not), with
    drill-down to the extracted fields, report text and GitLab link.
  - Data coverage: how much of each week has been extracted.

Reads the database fresh on each load (short cache), so new extraction
batches / summaries show up without restarting.

Usage:
    streamlit run src/dashboard.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import altair as alt
import pandas as pd
import streamlit as st

from src.config import (DATA_CUTOFF_DATE, EXCLUDE_RECENT_WEEKS,
                        MIN_WEEK_EXTRACTION_COVERAGE, SUMMARY_MODEL)
from src.db import get_connection
from src.detect_patterns import (load_series, evaluate_category, evaluate_total,
                                 weekly_coverage, coverage_start_week)

# Reference palette (dataviz skill): slot 1 blue for data, neutral inks
# for annotations. Status meaning is carried by icon + label, not color.
SERIES = "#2a78d6"
MUTED = "#8a8984"
SHADE = "#8a8984"

st.set_page_config(page_title="Incident Patterns", layout="wide")


# ---------------------------------------------------------------- data

@st.cache_data(ttl=60)
def load_incidents():
    conn = get_connection()
    df = pd.read_sql_query("""
        SELECT r.id, r.created_at, r.title, r.source_url, r.raw_text,
               r.severity_label, r.root_cause_label,
               s.failure_category, s.severity_assessment,
               s.root_cause_summary, s.affected_service, s.llm_model
        FROM raw_incidents r
        LEFT JOIN structured_incidents s ON s.incident_id = r.id
        WHERE r.created_at >= ?
        ORDER BY r.created_at DESC
    """, conn, params=(DATA_CUTOFF_DATE,))
    conn.close()
    df["created_at"] = pd.to_datetime(df["created_at"]).dt.tz_localize(None)
    df["extracted"] = df["failure_category"].notna()
    return df


@st.cache_data(ttl=60)
def load_patterns():
    conn = get_connection()
    try:
        df = pd.read_sql_query(
            "SELECT * FROM pattern_summaries WHERE active = 1", conn)
    except Exception:
        df = pd.DataFrame()
    conn.close()
    return df


@st.cache_data(ttl=60)
def load_coverage():
    conn = get_connection()
    cov = weekly_coverage(conn)
    start = coverage_start_week(conn)
    conn.close()
    return cov, start


@st.cache_data(ttl=60)
def load_trends():
    """Full weekly series (unfiltered) + detection results per category."""
    conn = get_connection()
    full = pd.read_sql_query(
        "SELECT week_start, failure_category, count FROM weekly_category_counts", conn)
    analysed = load_series(conn)  # coverage-filtered, what detection uses
    conn.close()
    results = {}
    if not analysed.empty:
        for cat in sorted(analysed["failure_category"].unique()):
            r = evaluate_category(analysed, cat)
            results[cat] = {k: r[k] for k in
                            ("status", "total", "usable_weeks", "anomalies", "changepoints")}
            results[cat]["usable_index"] = [str(d.date()) for d in r["usable"].index]
        t = evaluate_total(analysed)
        results["__total__"] = {k: t.get(k) for k in
                                ("status", "total", "usable_weeks", "anomalies",
                                 "changepoints", "baseline")}
        results["__total__"]["usable_index"] = [str(d.date()) for d in t["usable"].index]
    return full, results


incidents = load_incidents()
patterns = load_patterns()
coverage, coverage_start = load_coverage()

# ---------------------------------------------------------------- header

st.title("Incident postmortem patterns")
st.caption("GitLab production incidents · "
           f"window from {DATA_CUTOFF_DATE} · read-only view of the pipeline")

n_total = len(incidents)
n_extracted = int(incidents["extracted"].sum())
n_real = int(patterns["coherent"].sum()) if not patterns.empty else 0
c1, c2, c3, c4 = st.columns(4)
c1.metric("Incidents in window", f"{n_total:,}")
c2.metric("Extracted", f"{n_extracted:,}",
          f"{n_extracted / n_total:.0%} of window" if n_total else None,
          delta_color="off")
c3.metric("Active findings", len(patterns))
c4.metric("Judged real patterns", n_real)

tab_patterns, tab_trends, tab_search, tab_cov = st.tabs(
    ["Patterns", "Trends", "Search incidents", "Data coverage"])


# ---------------------------------------------------------------- patterns

TYPE_LABEL = {"cluster": "Recurring root cause",
              "changepoint": "Change in share of incidents",
              "anomaly": "Unusual week"}
CONF_ORDER = {"high": 0, "medium": 1, "low": 2}


def describe_stats(row):
    s = json.loads(row["stats_json"])
    if row["finding_type"] == "cluster":
        cats = ", ".join(s["categories"])
        return f"{s['size']} incidents · categories: {cats}"
    if row["finding_type"] == "changepoint":
        share = (f" (share {s['share_before']:.0%} → {s['share_after']:.0%})"
                 if "share_before" in s else "")
        return (f"{row['category']} · from week of {s['change_week']}: "
                f"{s['weekly_mean_before']}/week → {s['weekly_mean_after']}/week{share}")
    if "typical_week" in s:
        return (f"{row['category']} · week of {s['week']}: {s['count']} incidents "
                f"(typical week ≈ {s['typical_week']:.0f})")
    if "expected_from_week_volume" in s:
        return (f"{row['category']} · week of {s['week']}: {s['count']} incidents, "
                f"≈{s['expected_from_week_volume']:.1f} expected from that week's "
                f"{s['all_incidents_that_week']} total")
    return (f"{row['category']} · week of {s['week']}: {s['count']} incidents "
            f"(usual ≈ {s['category_weekly_mean']}/week, z = {s['z_score']})")


with tab_patterns:
    if patterns.empty:
        st.info("No summaries yet - run `python -m src.summarize`.")
    else:
        f1, f2 = st.columns([1, 2])
        only_real = f1.toggle("Only patterns judged real", value=True)
        types = f2.multiselect("Finding type", list(TYPE_LABEL),
                               default=list(TYPE_LABEL),
                               format_func=TYPE_LABEL.get)
        view = patterns[patterns["finding_type"].isin(types)]
        if only_real:
            view = view[view["coherent"] == 1]
        view = view.assign(_c=view["confidence"].map(CONF_ORDER)).sort_values(
            ["coherent", "_c"], ascending=[False, True])
        st.caption(f"Showing {len(view)} of {len(patterns)} findings. "
                   "Each explanation was written from only the incidents listed under it.")

        by_id = incidents.set_index("id")
        for _, row in view.iterrows():
            with st.container(border=True):
                st.subheader(row["headline"], anchor=False)
                b = st.columns([1.3, 1.3, 1, 4])
                b[0].badge(TYPE_LABEL[row["finding_type"]], color="gray")
                if row["coherent"]:
                    b[1].badge("Real pattern", icon=":material/check_circle:", color="blue")
                else:
                    b[1].badge("Likely not real", icon=":material/help:", color="gray")
                b[2].badge(f"{row['confidence']} confidence", color="gray")
                st.caption(describe_stats(row))
                st.write(row["explanation"])
                if row["caveats"]:
                    st.caption(f"**Caveats:** {row['caveats']}")
                if row["llm_model"] != SUMMARY_MODEL:
                    st.caption(f":material/warning: Written by fallback model "
                               f"`{row['llm_model']}` - its verdicts have disagreed "
                               f"with `{SUMMARY_MODEL}` before; treat as provisional.")

                ev = json.loads(row["evidence_ids_json"])
                cited = set(json.loads(row["cited_ids_json"]))
                with st.expander(f"Evidence · {len(ev)} incidents ({len(cited)} cited)"):
                    rows = by_id.loc[[i for i in ev if i in by_id.index]].reset_index()
                    rows["cited"] = rows["id"].isin(cited)
                    st.dataframe(
                        rows[["cited", "created_at", "failure_category",
                              "root_cause_summary", "source_url"]],
                        hide_index=True, width="stretch",
                        column_config={
                            "cited": st.column_config.CheckboxColumn("Cited", width="small"),
                            "created_at": st.column_config.DateColumn("Date"),
                            "failure_category": "Category",
                            "root_cause_summary": st.column_config.TextColumn(
                                "Extracted root cause", width="large"),
                            "source_url": st.column_config.LinkColumn(
                                "Issue", display_text="open ↗"),
                        })


# ---------------------------------------------------------------- trends

def trend_chart(cat_df, weeks, used_weeks, result,
                expected_title="Expected from week's volume"):
    data = (pd.DataFrame({"week": weeks})
            .merge(cat_df, how="left", left_on="week", right_on="week_start")
            .fillna({"count": 0}))
    data["week"] = pd.to_datetime(data["week"])
    data["analysed"] = data["week"].dt.strftime("%Y-%m-%d").isin(used_weeks)

    x = alt.X("week:T", title=None, axis=alt.Axis(format="%b %d", grid=False))
    y = alt.Y("count:Q", title="incidents / week",
              axis=alt.Axis(tickMinStep=1, gridOpacity=0.4))

    # Shade the weeks detection ignores, so a reader sees why.
    excluded = data[~data["analysed"]].assign(
        end=lambda d: d["week"] + pd.Timedelta(days=7))
    layers = []
    if not excluded.empty:
        layers.append(alt.Chart(excluded).mark_rect(opacity=0.12, color=SHADE).encode(
            x="week:T", x2="end:T",
            tooltip=[alt.Tooltip("week:T", title="Not analysed", format="%b %d")]))

    line = alt.Chart(data).mark_line(color=SERIES, strokeWidth=2).encode(x=x, y=y)
    points = alt.Chart(data).mark_point(
        filled=True, size=40, color=SERIES, opacity=1).encode(
        x=x, y=y,
        tooltip=[alt.Tooltip("week:T", title="Week of", format="%b %d, %Y"),
                 alt.Tooltip("count:Q", title="Incidents")])
    hit = alt.Chart(data).mark_point(size=300, opacity=0).encode(
        x=x, y=y, tooltip=[alt.Tooltip("week:T", title="Week of", format="%b %d, %Y"),
                           alt.Tooltip("count:Q", title="Incidents")])
    layers += [line, points, hit]

    if result.get("changepoints"):
        cps = pd.DataFrame({"week": pd.to_datetime(result["changepoints"]),
                            "label": "rate change"})
        layers.append(alt.Chart(cps).mark_rule(
            color=MUTED, strokeDash=[4, 3], strokeWidth=1.5).encode(
            x="week:T", tooltip=[alt.Tooltip("week:T", title="Change-point", format="%b %d")]))
        # Label sits at the bottom edge, where it can't collide with peaks.
        layers.append(alt.Chart(cps).mark_text(
            align="left", dx=4, baseline="bottom", color=MUTED, fontSize=11).encode(
            x="week:T", y=alt.value(196), text="label:N"))
    if result.get("anomalies"):
        an = pd.DataFrame([{"week": pd.Timestamp(w), "count": c, "expected": e}
                           for w, c, _, e in result["anomalies"]])
        layers.append(alt.Chart(an).mark_point(
            shape="circle", size=220, strokeWidth=2, color=MUTED, filled=False).encode(
            x="week:T", y="count:Q",
            tooltip=[alt.Tooltip("week:T", title="Unusual week", format="%b %d"),
                     alt.Tooltip("count:Q", title="Incidents"),
                     alt.Tooltip("expected:Q", title=expected_title, format=".1f")]))
    return alt.layer(*layers).properties(height=200)


with tab_trends:
    full, results = load_trends()
    if full.empty:
        st.info("No aggregated data yet - run `python -m src.aggregate`.")
    else:
        st.caption(
            "Weekly incident counts per category. **Shaded weeks are not used for "
            f"detection**: weeks before {coverage_start or '—'} are less than "
            f"{MIN_WEEK_EXTRACTION_COVERAGE:.0%} extracted, and the newest "
            f"{EXCLUDE_RECENT_WEEKS} weeks may still have open incidents. "
            "Detection compares each category with that week's **total** incident volume, "
            "so a busy week for everything is flagged once, in the All incidents chart, "
            "not in every category. Dashed lines mark a change in "
            "the category's share of incidents; open circles mark weeks far above "
            "what the total volume predicts.")
        # Total volume first: category detection is volume-adjusted, so a
        # surge across everything only shows up here.
        weeks_all = [d.strftime("%Y-%m-%d") for d in pd.date_range(
            full["week_start"].min(), full["week_start"].max(), freq="W-MON")]
        tot = results.get("__total__")
        if tot and tot["status"] == "evaluated":
            total_df = (full.groupby("week_start", as_index=False)["count"].sum())
            st.markdown(f"**All incidents** — typical week ≈ {tot['baseline']:.0f} "
                        "(a surge here is not repeated in the per-category charts)")
            st.altair_chart(trend_chart(total_df, weeks_all, tot["usable_index"], tot,
                                        expected_title="Typical week"),
                            width="stretch")
        show_sparse = st.toggle("Include categories too sparse to evaluate", value=False)
        # Every week in range, including weeks with zero incidents overall -
        # otherwise the line would silently skip them.
        weeks = [d.strftime("%Y-%m-%d") for d in pd.date_range(
            full["week_start"].min(), full["week_start"].max(), freq="W-MON")]
        cats = [c for c in sorted(full["failure_category"].unique())
                if c != "__total__" and
                (show_sparse or results.get(c, {}).get("status") == "evaluated")]
        cats.sort(key=lambda c: -results.get(c, {}).get("total", 0))
        cols = st.columns(2)
        for i, cat in enumerate(cats):
            r = results.get(cat, {"status": "insufficient_weeks", "total": 0,
                                  "usable_index": []})
            with cols[i % 2]:
                note = {"evaluated": "", "too_sparse": " · too sparse to evaluate",
                        "insufficient_weeks": " · not enough weeks"}[r["status"]]
                st.markdown(f"**{cat}** — {r['total']} incidents in analysed weeks{note}")
                st.altair_chart(
                    trend_chart(full[full["failure_category"] == cat], weeks,
                                r["usable_index"], r),
                    width="stretch")


# ---------------------------------------------------------------- search

with tab_search:
    s1, s2, s3 = st.columns([2, 1.2, 1])
    query = s1.text_input("Search title, report text and extracted root cause",
                          placeholder="e.g. pgbouncer, migration lock, redis")
    cat_options = sorted(incidents["failure_category"].dropna().unique())
    cats_sel = s2.multiselect("Category", cat_options)
    extracted_only = s3.toggle("Extracted only", value=False)

    res = incidents
    if query:
        q = query.lower()
        hay = (res["title"].fillna("") + " " + res["raw_text"].fillna("") + " "
               + res["root_cause_summary"].fillna("")).str.lower()
        res = res[hay.str.contains(q, regex=False)]
    if cats_sel:
        res = res[res["failure_category"].isin(cats_sel)]
    if extracted_only:
        res = res[res["extracted"]]

    st.caption(f"{len(res):,} incidents")
    table = res.assign(category=res["failure_category"].fillna("— not extracted yet"))
    sel = st.dataframe(
        table[["created_at", "title", "category", "severity_label", "source_url"]],
        hide_index=True, width="stretch", height=380,
        on_select="rerun", selection_mode="single-row",
        column_config={
            "created_at": st.column_config.DateColumn("Date"),
            "title": st.column_config.TextColumn("Title", width="large"),
            "category": "Category",
            "severity_label": st.column_config.NumberColumn("GitLab severity", format="S%d"),
            "source_url": st.column_config.LinkColumn("Issue", display_text="open ↗"),
        })

    picked = sel.selection.rows if sel else []
    if picked:
        inc = table.iloc[picked[0]]
        with st.container(border=True):
            st.subheader(inc["title"], anchor=False)
            st.caption(f"#{inc['id']} · {inc['created_at']:%Y-%m-%d} · "
                       f"[open on GitLab ↗]({inc['source_url']})")
            if inc["extracted"]:
                d = st.columns(4)
                d[0].metric("Category (LLM)", inc["failure_category"])
                d[1].metric("Severity (LLM)", f"S{int(inc['severity_assessment'])}")
                d[2].metric("Severity (GitLab)",
                            f"S{int(inc['severity_label'])}" if pd.notna(inc["severity_label"]) else "—")
                d[3].metric("Root cause (GitLab)", inc["root_cause_label"] or "—")
                st.markdown(f"**Extracted root cause:** {inc['root_cause_summary']}")
                st.caption(f"Affected service: {inc['affected_service'] or '—'} · "
                           f"extracted by `{inc['llm_model']}`")
            else:
                st.info("Not extracted yet - it will be picked up by a future "
                        "`python -m src.extract` batch.")
            with st.expander("Full report text"):
                st.text(inc["raw_text"] or "(empty)")
    else:
        st.caption("Select a row to see its details.")


# ---------------------------------------------------------------- coverage

with tab_cov:
    if coverage.empty:
        st.info("No incidents in the window.")
    else:
        cov = coverage.reset_index()
        cov["status"] = "not used for detection"
        if coverage_start:
            cov.loc[cov["week_start"] >= pd.Timestamp(coverage_start), "status"] = "used for detection"
            cov.loc[cov.index[-EXCLUDE_RECENT_WEEKS:], "status"] = "not used for detection"
        shown = cov[cov["extracted"] > 0]
        first = shown["week_start"].min() if not shown.empty else cov["week_start"].min()
        cov = cov[cov["week_start"] >= first - pd.Timedelta(weeks=4)].copy()
        cov["pct"] = cov["coverage"] * 100
        # Bars span their week (minus a 1-day gap) instead of a hairline.
        cov["week_end"] = cov["week_start"] + pd.Timedelta(days=6)

        st.caption(
            f"Share of each week's incidents that have been extracted. Detection only "
            f"uses the newest unbroken run of weeks at ≥{MIN_WEEK_EXTRACTION_COVERAGE:.0%} "
            f"(minus the newest {EXCLUDE_RECENT_WEEKS}); everything else would show up "
            "as a false drop. Older weeks fill in as daily extraction batches run.")
        bars = alt.Chart(cov).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
            x=alt.X("week_start:T", title=None, axis=alt.Axis(format="%b %d", grid=False)),
            x2="week_end:T",
            y2=alt.datum(0),  # explicit baseline - with x2, a bar otherwise renders as a range
            y=alt.Y("pct:Q", title="% extracted", scale=alt.Scale(domain=[0, 100]),
                    axis=alt.Axis(gridOpacity=0.4)),
            color=alt.Color("status:N", title=None,
                            scale=alt.Scale(domain=["used for detection", "not used for detection"],
                                            range=[SERIES, "#c3c2b7"]),
                            legend=alt.Legend(orient="top")),
            tooltip=[alt.Tooltip("week_start:T", title="Week of", format="%b %d, %Y"),
                     alt.Tooltip("extracted:Q", title="Extracted"),
                     alt.Tooltip("raw:Q", title="Incidents"),
                     alt.Tooltip("pct:Q", title="% extracted", format=".0f"),
                     alt.Tooltip("status:N", title="Status")])
        rule = alt.Chart(pd.DataFrame({"y": [MIN_WEEK_EXTRACTION_COVERAGE * 100]})).mark_rule(
            color=MUTED, strokeDash=[4, 3]).encode(y="y:Q")
        st.altair_chart((bars + rule).properties(height=260), width="stretch")
        with st.expander("Table view"):
            st.dataframe(cov[["week_start", "raw", "extracted", "pct", "status"]],
                         hide_index=True, width="stretch",
                         column_config={"week_start": st.column_config.DateColumn("Week of"),
                                        "pct": st.column_config.NumberColumn("% extracted", format="%.0f%%")})
