"""
Phase 7 - Summarization.

Turns the findings from Phase 5 (change-points, z-score anomalies)
and Phase 6 (root-cause clusters) into short plain-language
explanations, grounded ONLY in the incidents that support each
finding.

Design points (see PROJECT_LOG.md for the reasoning):

  - Grounding: the LLM sees only the evidence incidents for a finding
    and must cite which ones support its explanation. Any cited id that
    wasn't in the evidence set is rejected; a response that cites
    nothing valid is retried, then skipped - an explanation that can't
    point at its sources isn't trustworthy enough to store.
  - Skeptical by design: Phases 3, 5 and 6 each produced results that
    LOOKED meaningful but were artifacts. So the LLM is explicitly asked
    whether the incidents genuinely share a cause ("coherent"), and is
    allowed to say no. Weak clusters get labeled weak, not dressed up.
  - Idempotent: each finding has a stable key plus a hash of its
    evidence. Unchanged findings are not re-summarized (no tokens
    spent); a finding whose evidence changed is regenerated.
  - Token-cheap: one call per finding (~10 total), not per incident.
    Uses SUMMARY_MODEL, falling back through LLM_MODEL_CHAIN only if
    that model's rate limit is hit (e.g. because extraction is using
    the same quota on the same day).

Usage:
    python -m src.summarize              # summarize new/changed findings
    python -m src.summarize --dry-run    # list findings, no LLM calls
    python -m src.summarize --force      # regenerate everything
"""

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Literal

import pandas as pd
from groq import RateLimitError, NotFoundError
from pydantic import BaseModel, Field, ValidationError

from src.config import SUMMARY_MODEL, LLM_MODEL_CHAIN
from src.db import get_connection, init_schema
from src import aggregate
from src.detect_patterns import load_series, evaluate_category
from src.cluster_root_causes import find_clusters
from src.llm_client import client, DailyRateLimitExceeded, ModelUnavailable

MAX_EVIDENCE = 8            # incidents shown to the LLM per finding
RAW_TEXT_CHARS = 700        # per-incident excerpt length (keeps calls small)
CHANGEPOINT_WINDOW_WEEKS = 3  # evidence = incidents within N weeks after a change-point


class PatternSummary(BaseModel):
    headline: str = Field(max_length=160)
    explanation: str
    cited_incident_ids: list[int]
    coherent: bool
    confidence: Literal["high", "medium", "low"]
    caveats: str | None = None


SYSTEM_PROMPT = """You explain patterns that an automated system flagged in a company's incident postmortems. Your reader is an engineer deciding whether the pattern deserves attention.

You will get: the type of finding, the statistics behind it, and the incidents that make up the evidence. Use ONLY that evidence - do not add outside knowledge about the company or guess at causes the incidents don't state.

Be skeptical. Automated pattern detection often flags things that are not real (incidents grouped by similar wording, an alert type, or chance). Decide whether the incidents genuinely share an underlying cause or trend.

Respond with ONLY a JSON object:
{
  "headline": "<= 15 words, specific (name the system / failure mode)",
  "explanation": "3-5 sentences: what the pattern is, what the incidents have in common (or don't), and why it matters",
  "cited_incident_ids": [ids from the evidence that directly support the explanation],
  "coherent": true if the incidents genuinely share a cause/trend, false if the grouping looks superficial,
  "confidence": "high" | "medium" | "low",
  "caveats": "limitations: small sample, mixed causes, missing root causes, etc. (or null)"
}"""


# ---------------------------------------------------------------- findings

def _week_bounds(week_start):
    start = pd.Timestamp(week_start)
    return start, start + timedelta(days=7)


def _incidents_in_range(conn, category, start, end):
    df = pd.read_sql_query("""
        SELECT s.incident_id, r.created_at
        FROM structured_incidents s
        JOIN raw_incidents r ON s.incident_id = r.id
        WHERE s.failure_category = ?
    """, conn, params=(category,))
    df["created_at"] = pd.to_datetime(df["created_at"]).dt.tz_localize(None)
    df = df[(df["created_at"] >= start) & (df["created_at"] < end)]
    return df.sort_values("created_at")["incident_id"].tolist()


def collect_findings():
    """
    Gathers every finding Phases 5 and 6 currently flag, as dicts with
    a stable key, the stats behind it, and its evidence incident ids.
    Re-runs aggregation first so the weekly counts match the latest
    extraction batch (cheap full recompute).
    """
    aggregate.run()
    findings = []

    conn = get_connection()
    series_df = load_series(conn)
    if not series_df.empty:
        for category in sorted(series_df["failure_category"].unique()):
            r = evaluate_category(series_df, category)
            if r["status"] != "evaluated":
                continue
            usable = r["usable"]

            for cp in r["changepoints"]:
                cp_ts = pd.Timestamp(cp)
                before = usable[usable.index < cp_ts]
                after = usable[usable.index >= cp_ts]
                ids = _incidents_in_range(
                    conn, category, cp_ts,
                    cp_ts + timedelta(weeks=CHANGEPOINT_WINDOW_WEEKS))
                findings.append({
                    "key": f"changepoint:{category}:{cp}",
                    "type": "changepoint",
                    "category": category,
                    "stats": {
                        "change_week": str(cp),
                        "weekly_mean_before": round(float(before.mean()), 2),
                        "weekly_mean_after": round(float(after.mean()), 2),
                        "weeks_before": len(before),
                        "weeks_after": len(after),
                    },
                    "evidence_ids": ids[:MAX_EVIDENCE],
                })

            for week, count, z in r["anomalies"]:
                if count == 0:
                    continue  # a "quiet week" has no incidents to explain
                start, end = _week_bounds(week)
                ids = _incidents_in_range(conn, category, start, end)
                findings.append({
                    "key": f"anomaly:{category}:{week}",
                    "type": "anomaly",
                    "category": category,
                    "stats": {
                        "week": str(week),
                        "count": int(count),
                        "z_score": float(z),
                        "category_weekly_mean": round(float(usable.mean()), 2),
                    },
                    "evidence_ids": ids[:MAX_EVIDENCE],
                })
    conn.close()

    clusters = find_clusters()
    if clusters is not None:
        for cid in sorted(c for c in clusters["cluster"].unique() if c != -1):
            members = clusters[clusters["cluster"] == cid]
            ids = sorted(int(i) for i in members["incident_id"])
            # Evidence = the most central members (not the lowest ids), so
            # a large cluster is explained from its core, not its fringe.
            core = [int(i) for i in members.sort_values(
                "membership", ascending=False)["incident_id"].head(MAX_EVIDENCE)]
            cats = sorted(members["failure_category"].unique())
            # HDBSCAN cluster numbers aren't stable between runs, so key
            # clusters by their membership instead.
            member_hash = hashlib.sha1(",".join(map(str, ids)).encode()).hexdigest()[:10]
            findings.append({
                "key": f"cluster:{member_hash}",
                "type": "cluster",
                "category": None,
                "stats": {
                    "size": len(ids),
                    "categories": cats,
                    "spans_multiple_categories": len(cats) > 1,
                },
                "evidence_ids": sorted(core),
            })

    for f in findings:
        f["evidence_hash"] = hashlib.sha1(
            json.dumps([f["stats"], f["evidence_ids"]], sort_keys=True).encode()
        ).hexdigest()
    return findings


# ---------------------------------------------------------------- LLM call

def _load_evidence(conn, ids):
    if not ids:
        return pd.DataFrame()
    q = ",".join("?" * len(ids))
    return pd.read_sql_query(f"""
        SELECT r.id, r.created_at, r.title, r.raw_text, r.source_url,
               s.failure_category, s.root_cause_summary
        FROM raw_incidents r JOIN structured_incidents s ON s.incident_id = r.id
        WHERE r.id IN ({q})
        ORDER BY r.created_at
    """, conn, params=ids)


def _build_prompt(finding, evidence):
    lines = [
        f"FINDING TYPE: {finding['type']}",
        f"CATEGORY: {finding['category'] or '(cluster - categories listed in stats)'}",
        f"STATS: {json.dumps(finding['stats'])}",
        "",
        f"EVIDENCE ({len(evidence)} incidents):",
    ]
    for _, row in evidence.iterrows():
        excerpt = " ".join((row["raw_text"] or "").split())[:RAW_TEXT_CHARS]
        lines += [
            f"--- incident id={row['id']} | {row['created_at'][:10]} "
            f"| category={row['failure_category']}",
            f"title: {row['title']}",
            f"extracted root cause: {row['root_cause_summary']}",
            f"report excerpt: {excerpt}",
        ]
    return "\n".join(lines)


def _call(prompt, model):
    response = client.chat.completions.create(
        model=model,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )
    return json.loads(response.choices[0].message.content)


def summarize_finding(finding, evidence, model, max_retries=1):
    """
    Returns a grounded PatternSummary, or None if the model can't
    produce one that cites real evidence. Raises DailyRateLimitExceeded
    so the caller can switch models.
    """
    allowed = set(int(i) for i in evidence["id"])
    prompt = _build_prompt(finding, evidence)
    last_error = None

    for _ in range(max_retries + 1):
        try:
            result = PatternSummary.model_validate(_call(prompt, model))
        except RateLimitError as e:
            raise DailyRateLimitExceeded(str(e))
        except NotFoundError as e:
            raise ModelUnavailable(str(e))
        except (ValidationError, json.JSONDecodeError, Exception) as e:
            last_error = e
            continue

        cited = [i for i in result.cited_incident_ids if i in allowed]
        dropped = set(result.cited_incident_ids) - allowed
        if dropped:
            print(f"    dropped {len(dropped)} cited id(s) not in evidence: {sorted(dropped)}")
        if not cited:
            last_error = "no valid evidence cited"
            continue
        result.cited_incident_ids = cited
        return result

    print(f"    could not get a grounded summary: {last_error}")
    return None


# ---------------------------------------------------------------- storage

def _existing(conn):
    rows = conn.execute(
        "SELECT finding_key, evidence_hash FROM pattern_summaries").fetchall()
    return dict(rows)


def _save(conn, finding, summary, model):
    conn.execute("""
        INSERT OR REPLACE INTO pattern_summaries
            (finding_key, finding_type, category, stats_json, evidence_ids_json,
             evidence_hash, headline, explanation, cited_ids_json, coherent,
             confidence, caveats, llm_model, generated_at, active)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
    """, (
        finding["key"], finding["type"], finding["category"],
        json.dumps(finding["stats"]), json.dumps(finding["evidence_ids"]),
        finding["evidence_hash"], summary.headline, summary.explanation,
        json.dumps(summary.cited_incident_ids), int(summary.coherent),
        summary.confidence, summary.caveats, model,
        datetime.now(timezone.utc).isoformat(),
    ))
    conn.commit()


def _mark_active(conn, keys):
    conn.execute("UPDATE pattern_summaries SET active = 0")
    conn.executemany(
        "UPDATE pattern_summaries SET active = 1 WHERE finding_key = ?",
        [(k,) for k in keys])
    conn.commit()


# ---------------------------------------------------------------- main

def run(dry_run=False, force=False):
    init_schema()
    findings = collect_findings()
    print(f"\n{len(findings)} findings flagged by Phases 5-6.")

    conn = get_connection()
    existing = _existing(conn)
    todo = [f for f in findings
            if force or existing.get(f["key"]) != f["evidence_hash"]]
    todo = [f for f in todo if f["evidence_ids"]]
    print(f"{len(findings) - len(todo)} already summarized and unchanged; "
          f"{len(todo)} to summarize (~{len(todo)} LLM calls).\n")

    if dry_run:
        for f in findings:
            mark = "*" if f in todo else " "
            print(f" {mark} {f['key']:<45} {len(f['evidence_ids'])} incidents  {f['stats']}")
        print("\n(* = would be summarized. Dry run - no LLM calls made.)")
        conn.close()
        return

    models = [SUMMARY_MODEL] + [m for m in LLM_MODEL_CHAIN if m != SUMMARY_MODEL]
    model_idx = 0
    done = skipped = 0

    for f in todo:
        evidence = _load_evidence(conn, f["evidence_ids"])
        print(f"[{f['key']}] {len(evidence)} evidence incidents")
        while True:
            try:
                summary = summarize_finding(f, evidence, models[model_idx])
                break
            except DailyRateLimitExceeded:
                model_idx += 1
                if model_idx >= len(models):
                    print("All models rate-limited - stopping. Re-run later; "
                          "finished summaries are saved and won't be redone.")
                    _mark_active(conn, [x["key"] for x in findings])
                    conn.close()
                    return
                print(f"    rate limit hit / model unavailable - switching to {models[model_idx]}")

        if summary is None:
            skipped += 1
            continue
        _save(conn, f, summary, models[model_idx])
        done += 1
        verdict = "coherent" if summary.coherent else "NOT coherent"
        print(f"    -> {summary.headline}  [{verdict}, {summary.confidence}]")

    _mark_active(conn, [f["key"] for f in findings])
    conn.close()
    print(f"\nDone: {done} summarized, {skipped} skipped, "
          f"{len(findings) - len(todo)} unchanged.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 7 - explain flagged patterns")
    parser.add_argument("--dry-run", action="store_true",
                        help="list findings without calling the LLM")
    parser.add_argument("--force", action="store_true",
                        help="regenerate summaries even if evidence is unchanged")
    args = parser.parse_args()
    run(dry_run=args.dry_run, force=args.force)
