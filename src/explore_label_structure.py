"""
Explores the label STRUCTURE of the already-pulled dataset
(data/raw/issues_raw.json) to answer: how much of our planned
extraction schema (severity, root cause, category) already
exists as clean label metadata, versus how much genuinely
needs to be inferred from free text?

This does NOT call the GitLab API — it reads the file already
saved by explore_incident_dataset.py.

Usage:
    python -m src.explore_label_structure
"""

import json
from collections import Counter, defaultdict

from src.config import RAW_DATA_PATH


def label_namespace(label: str) -> str:
    """
    Labels like 'severity::3' or 'RootCause::Saturation' use GitLab's
    '::' scoped-label convention. This pulls out the namespace
    ('severity', 'RootCause') so we can group by taxonomy rather
    than by every individual label value.
    """
    if "::" in label:
        return label.split("::")[0]
    return "(unscoped)"


def run():
    with open(RAW_DATA_PATH) as f:
        issues = json.load(f)

    print(f"Loaded {len(issues)} issues from local cache.\n")

    # --- 1. Namespace frequency: which scoped-label taxonomies exist? ---
    namespace_counter = Counter()
    for issue in issues:
        for lbl in issue.get("labels", []):
            namespace_counter[label_namespace(lbl)] += 1

    print("Label namespaces by frequency (top 15):")
    for ns, count in namespace_counter.most_common(15):
        print(f"  {ns}: {count}")

    # --- 2. Coverage: what % of issues have a severity label at all? ---
    has_severity = sum(
        1 for i in issues
        if any(lbl.startswith("severity::") for lbl in i.get("labels", []))
    )
    has_root_cause = sum(
        1 for i in issues
        if any(lbl.startswith("RootCause::") for lbl in i.get("labels", []))
    )
    has_incident_tag = sum(
        1 for i in issues if "incident" in i.get("labels", [])
    )

    total = len(issues)
    print(f"\nCoverage of existing structured labels:")
    print(f"  Has a severity::* label:   {has_severity} / {total} "
          f"({has_severity/total:.0%})")
    print(f"  Has a RootCause::* label:  {has_root_cause} / {total} "
          f"({has_root_cause/total:.0%})")
    print(f"  Tagged 'incident':         {has_incident_tag} / {total} "
          f"({has_incident_tag/total:.0%})")

    # --- 3. Co-occurrence: severity label AND a real (long) description ---
    def desc_len(i):
        return len(i.get("description") or "")

    severity_and_long_desc = sum(
        1 for i in issues
        if any(lbl.startswith("severity::") for lbl in i.get("labels", []))
        and desc_len(i) > 500
    )
    print(f"\n  Has severity label AND description > 500 chars: "
          f"{severity_and_long_desc} / {total} "
          f"({severity_and_long_desc/total:.0%})")
    print("  -> this is your strongest candidate validation set for Stage 4:")
    print("     ground truth severity already exists, paired with real")
    print("     narrative text for the LLM to extract root cause/category from.")

    # --- 4. What does NOT have structured labels? (genuinely needs LLM/NER) ---
    no_severity_but_long_desc = sum(
        1 for i in issues
        if not any(lbl.startswith("severity::") for lbl in i.get("labels", []))
        and desc_len(i) > 500
    )
    print(f"\n  Long description but NO severity label: "
          f"{no_severity_but_long_desc} / {total} "
          f"({no_severity_but_long_desc/total:.0%})")
    print("  -> this slice genuinely needs LLM extraction, no shortcut available.")

    # --- 5. RootCause label breakdown (candidate for failure_category schema) ---
    root_cause_values = Counter()
    for issue in issues:
        for lbl in issue.get("labels", []):
            if lbl.startswith("RootCause::"):
                root_cause_values[lbl] += 1

    print(f"\nExisting RootCause::* values (candidate categories for your schema):")
    for val, count in root_cause_values.most_common(20):
        print(f"  {val}: {count}")


if __name__ == "__main__":
    run()