"""
Explores the raw GitLab issue dataset before any extraction or
aggregation code gets written. Answers three questions:
  1. How many issues are there, really?
  2. What date range do they span?
  3. What labels exist, and how many issues look like genuine
     postmortems vs. short alerts?

Usage:
    python -m src.explore_incident_dataset            # quick test, 3 pages only
    python -m src.explore_incident_dataset --full      # pulls everything (slower)
"""

import sys
import os
import json
from collections import Counter
from datetime import datetime

from src.gitlab_client import fetch_all_issues, fetch_project_labels
from src.config import RAW_DATA_PATH


def run(full_pull: bool):
    max_pages = None if full_pull else 3

    print("Fetching project labels...")
    labels = fetch_project_labels()
    print(f"Found {len(labels)} labels. Sample: {labels[:15]}")

    print("\nFetching issues (state=closed)...")
    issues = fetch_all_issues(state="closed", max_pages=max_pages)

    print(f"\nTotal issues pulled: {len(issues)}")

    if not issues:
        print("No issues returned — check your token/project path.")
        sys.exit(1)

    # Date range
    dates = [datetime.fromisoformat(i["created_at"].replace("Z", "+00:00")) for i in issues]
    print(f"Date range: {min(dates).date()} to {max(dates).date()}")

    # Label distribution — this tells us which label (if any) marks
    # genuine postmortems vs. short infra alerts
    label_counter = Counter()
    for issue in issues:
        for lbl in issue.get("labels", []):
            label_counter[lbl] += 1

    print("\nTop 20 labels by frequency:")
    for lbl, count in label_counter.most_common(20):
        print(f"  {lbl}: {count}")

    # Rough proxy for "genuine postmortem" content: description length.
    # Short alerts tend to have near-empty descriptions; real postmortems
    # (5-whys, timeline, corrective actions) are long.
    desc_lengths = [len(i.get("description") or "") for i in issues]
    long_desc_count = sum(1 for d in desc_lengths if d > 500)
    print(f"\nIssues with description > 500 chars (likely real postmortems): "
          f"{long_desc_count} / {len(issues)}")

    # Save raw pull so we don't have to re-fetch while inspecting
    os.makedirs(os.path.dirname(RAW_DATA_PATH), exist_ok=True)
    with open(RAW_DATA_PATH, "w") as f:
        json.dump(issues, f)
    print(f"\nSaved raw pull to {RAW_DATA_PATH}")


if __name__ == "__main__":
    run(full_pull="--full" in sys.argv)