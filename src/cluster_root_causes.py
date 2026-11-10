"""
Phase 6 - Semantic clustering / dedup.

Embeds every root_cause_summary and clusters them by semantic
similarity - independent of what failure_category the LLM assigned.
This catches two distinct things categorization alone misses:

  1. TRUE recurring root causes: two incidents described in
     different words that are actually the same underlying problem
     (e.g. "database connection pool exhausted" vs "DB pool ran out
     of connections during peak load") - keyword/category matching
     alone would treat these as unrelated.
  2. EXTRACTION INCONSISTENCY: two semantically near-identical
     summaries that the LLM happened to sort into DIFFERENT
     failure_category values - a sign the category assignment
     isn't fully reliable/consistent for that kind of incident.

Uses bge-small-en-v1.5 (same embedding model as the FilingLens
project) + HDBSCAN (density-based, doesn't require pre-choosing
the number of clusters - appropriate here since we don't know how
many "true" root-cause groups exist ahead of time).

Setup (one-time):
    pip install sentence-transformers scikit-learn

Usage:
    python -m src.cluster_root_causes
"""

import pandas as pd
from sentence_transformers import SentenceTransformer
from sklearn.cluster import HDBSCAN
from sklearn.preprocessing import normalize

from src.db import get_connection

MODEL_NAME = "BAAI/bge-small-en-v1.5"
MIN_CLUSTER_SIZE = 3  # a cluster needs at least this many members to count

# Phrases indicating the LLM gave a boilerplate "couldn't determine
# anything" answer rather than a real root-cause description. These
# summaries cluster together purely because the REFUSAL PHRASING is
# similar, not because the underlying incidents share anything - a
# confound discovered by inspecting real cluster output (clusters
# 8-14 in the first run were all "no root cause identified"
# variants, not genuine recurring patterns). Filtered out before
# embedding so real content clusters aren't buried among these.
BOILERPLATE_MARKERS = [
    "no root cause",
    "not discernible",
    "no incident",
    "planned change",
    "no failure",
    # Added after Phase 6 re-run: non-answers phrased differently that
    # slipped through and formed a false "blackbox probe" cluster.
    # Deliberately NOT "unclear"/bare "unknown" - those match real causes
    # (e.g. "left the database in an unclear state").
    "not yet determined",
    "not been identified",
    "root cause not",
    "is unknown",
    # Added 2026-11-06 at 1,032 records: phrasings from the fallback
    # models (gpt-oss-20b, qwen3.8). Only PURE non-answers - summaries
    # that describe a symptom but no cause ("external-dns stopped
    # syncing...; cause not identified") are kept, since a recurring
    # symptom is still worth clustering.
    "root cause was not identified",
    "root cause is currently unknown",
    "root cause unknown",
    "caused by unknown factors",
    "no identified root cause",
    "not specified in the provided",
    "no definitive root cause",
]


def is_boilerplate(summary: str) -> bool:
    s = (summary or "").lower()
    return any(marker in s for marker in BOILERPLATE_MARKERS)


def load_summaries(conn):
    return pd.read_sql_query("""
        SELECT s.incident_id, s.failure_category, s.root_cause_summary, r.source_url,
               s.root_cause_identified
        FROM structured_incidents s
        JOIN raw_incidents r ON s.incident_id = r.id
        WHERE s.root_cause_summary IS NOT NULL AND length(s.root_cause_summary) > 0
    """, conn)


def find_clusters():
    """
    Filters, embeds and clusters all summaries. Returns the summaries
    DataFrame with a "cluster" column (-1 = unclustered), or None if
    there isn't enough data. Shared by run() (prints) and Phase 7's
    summarize.py (explains clusters) so both see identical clusters.
    """
    conn = get_connection()
    df = load_summaries(conn)
    conn.close()

    if len(df) < MIN_CLUSTER_SIZE:
        print(f"Only {len(df)} summaries available - need at least "
              f"{MIN_CLUSTER_SIZE} for clustering to be meaningful. "
              f"Run more extraction batches first.")
        return None

    # A summary is dropped if the model said no root cause was found
    # (root_cause_identified = 0, records extracted since 2026-11-10) OR
    # its wording matches a known non-answer. The markers stay as a safety
    # net and are the only signal for older records (flag is NULL).
    before = len(df)
    flag = df["root_cause_identified"]
    by_marker = df["root_cause_summary"].apply(is_boilerplate)
    by_flag = flag == 0
    has_flag = flag.notna()
    df = df[~(by_marker | by_flag)].reset_index(drop=True)
    print(f"Filtered out {before - len(df)} 'no root cause identified' summaries "
          f"({len(df)} substantive summaries remain).")
    if has_flag.any():
        # How well the phrase list agrees with the model's own answer, on
        # records that have both - tells us whether the list is still needed.
        print(f"  root_cause_identified set on {int(has_flag.sum())} records: "
              f"{int((by_flag & ~by_marker).sum())} caught only by the flag, "
              f"{int((by_marker & has_flag & ~by_flag).sum())} only by phrase markers.")

    if len(df) < MIN_CLUSTER_SIZE:
        print(f"After filtering, only {len(df)} substantive summaries remain - "
              f"not enough to cluster yet.")
        return None

    print(f"Embedding {len(df)} root-cause summaries "
          f"(one-time model download on first run)...")
    model = SentenceTransformer(MODEL_NAME)
    embeddings = model.encode(df["root_cause_summary"].tolist(), show_progress_bar=True)

    # L2-normalize so euclidean distance behaves like cosine similarity -
    # sentence embeddings are meant to be compared by direction, not raw
    # distance. Without this, HDBSCAN was "chaining" loosely-related
    # incidents into one giant catch-all cluster (119 of 159 incidents
    # in one blob spanning 13 categories - not a real finding).
    embeddings = normalize(embeddings)

    print(f"\nClustering with HDBSCAN (min_cluster_size={MIN_CLUSTER_SIZE})...")
    clusterer = HDBSCAN(
        min_cluster_size=MIN_CLUSTER_SIZE,
        metric="euclidean",
        cluster_selection_method="leaf",  # favors more, smaller, tighter
                                           # clusters over few large ones -
                                           # counters the chaining behavior
    )
    df["cluster"] = clusterer.fit_predict(embeddings)
    # Membership strength (0-1): how firmly each incident sits in its
    # cluster's dense core vs. its fringe. Phase 7 uses it to explain a
    # large cluster from its core members - at 1,032 records the
    # migration-lock cluster had grown to 36 with ~10 fringe "deployment
    # failed" incidents chained on (see PROJECT_LOG.md).
    df["membership"] = clusterer.probabilities_
    return df


def run():
    df = find_clusters()
    if df is None:
        return
    labels = df["cluster"].values

    n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
    n_noise = int((labels == -1).sum())
    print(f"Found {n_clusters} clusters ({n_noise} summaries didn't fit any "
          f"cluster - treated as unique, not forced into a group).\n")

    if n_clusters == 0:
        print("No clusters found yet - likely too little data or too much "
              "variety at this volume. Expected to improve as more "
              "incidents accumulate.")
        return

    for cluster_id in sorted(set(labels)):
        if cluster_id == -1:
            continue
        members = df[df["cluster"] == cluster_id]
        categories = members["failure_category"].unique()

        print(f"--- Cluster {cluster_id} ({len(members)} incidents) ---")
        flag = " <-- SPANS MULTIPLE CATEGORIES" if len(categories) > 1 else ""
        print(f"Categories present: {list(categories)}{flag}")
        for _, row in members.iterrows():
            print(f"  [{row['failure_category']}] {row['root_cause_summary'][:100]}")
        print()


if __name__ == "__main__":
    run()