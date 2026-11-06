"""
Phase 3 - Cross-check validation.

Measures how trustworthy Phase 2's LLM extraction actually is,
using three independent checks against whatever is currently in
structured_incidents (works regardless of the 3-year window -
validation tests EXTRACTION ACCURACY, not recency):

  1. severity_assessment vs GitLab's real severity::N label
  2. failure_category vs GitLab's real RootCause::X label
  3. affected_service vs GitLab's real Service::X label (primary,
     where present) and spaCy NER (fallback + grounding check,
     used for the majority of records with no Service:: label)

Read-only - measures and reports, does not modify structured_incidents.

Usage:
    python -m src.validate
"""

import json
from collections import Counter

from src.db import get_connection
from src.spacy_ner import extract_entities


def normalize(s):
    return (s or "").strip().lower()


def fuzzy_match(a, b):
    """
    True if normalized strings meaningfully overlap - substring
    containment either direction, or >=50% token (word) overlap.
    Used for affected_service, which is free text on both sides
    (no fixed vocabulary), unlike severity/category which use
    exact equality.
    """
    a, b = normalize(a), normalize(b)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    a_tokens, b_tokens = set(a.split()), set(b.split())
    if not a_tokens or not b_tokens:
        return False
    overlap = len(a_tokens & b_tokens) / len(a_tokens | b_tokens)
    return overlap >= 0.5


def get_service_labels(labels_json):
    labels = json.loads(labels_json) if labels_json else []
    return [lbl.split("::", 1)[1] for lbl in labels if lbl.startswith("Service::")]


def print_confusion_matrix(pairs, title):
    """pairs: list of (ground_truth_value, llm_predicted_value) tuples."""
    print(f"\n--- {title} ---")
    total = len(pairs)
    if total == 0:
        print("No records with ground truth available for this check.")
        return

    matches = sum(1 for t, p in pairs if t == p)
    print(f"Exact match accuracy: {matches}/{total} ({matches/total:.1%})")

    matrix = Counter(pairs)
    true_values = sorted(set(t for t, p in pairs), key=str)

    print(f"\nConfusion matrix (rows = ground truth, cols = LLM prediction):")
    header = "GT \\ Pred".ljust(20)
    for v in true_values:
        header += str(v)[:9].ljust(10)
    print(header)

    for true_v in true_values:
        row = str(true_v)[:18].ljust(20)
        for pred_v in true_values:
            row += str(matrix.get((true_v, pred_v), 0)).ljust(10)
        print(row)


def run():
    conn = get_connection()
    rows = conn.execute("""
        SELECT s.incident_id, s.severity_assessment, s.failure_category,
               s.affected_service, r.severity_label, r.root_cause_label,
               r.labels_json, r.raw_text, s.llm_model
        FROM structured_incidents s
        JOIN raw_incidents r ON s.incident_id = r.id
    """).fetchall()
    conn.close()

    print(f"Validating {len(rows)} extracted incidents...")

    # --- Track 1: severity ---
    severity_pairs = [(r[4], r[1]) for r in rows if r[4] is not None]
    print_confusion_matrix(severity_pairs, "SEVERITY (ground truth vs LLM)")

    # --- Track 2: root cause category ---
    rootcause_pairs = [(r[5], r[2]) for r in rows if r[5] is not None]
    print_confusion_matrix(rootcause_pairs, "ROOT CAUSE CATEGORY (ground truth vs LLM)")

    # --- Track 3: affected service (ground truth OR NER fallback) ---
    print(f"\n--- AFFECTED SERVICE ---")
    gt_matches, gt_total = 0, 0
    ner_matches, ner_total = 0, 0
    no_check_possible = 0

    for (incident_id, severity_assess, failure_cat, llm_service,
         sev_label, rc_label, labels_json, raw_text, _model) in rows:

        service_labels = get_service_labels(labels_json)

        if service_labels:
            gt_total += 1
            if any(fuzzy_match(llm_service, sl) for sl in service_labels):
                gt_matches += 1
        else:
            entities = extract_entities(raw_text)
            if entities:
                ner_total += 1
                if any(fuzzy_match(llm_service, e) for e in entities):
                    ner_matches += 1
            else:
                no_check_possible += 1

    if gt_total:
        print(f"Against Service::* ground truth: {gt_matches}/{gt_total} "
              f"({gt_matches/gt_total:.1%})")
    else:
        print("No records with Service::* ground truth in this batch.")

    if ner_total:
        print(f"Against spaCy NER (fallback, no ground truth available): "
              f"{ner_matches}/{ner_total} ({ner_matches/ner_total:.1%})")
    else:
        print("No NER fallback checks were possible.")

    print(f"No check possible at all (no label, no NER entities found): "
          f"{no_check_possible}")

    print_per_model(rows)


def _acc(pairs):
    if not pairs:
        return "      n/a"
    hits = sum(1 for gt, pred in pairs if normalize(str(gt)) == normalize(str(pred)))
    return f"{hits:>3}/{len(pairs):<3} {hits / len(pairs):>4.0%}"


def print_per_model(rows):
    """
    Same ground-truth checks, split by the model that did the extraction.
    Needed because the fallback chain means a growing share of records
    come from smaller models (gpt-oss-20b, qwen) once the main model's
    daily quota runs out - their accuracy can't be assumed equal.
    Only GitLab-label ground truth is used here (no NER), so the
    numbers are comparable across models.
    """
    by_model = {}
    for r in rows:
        by_model.setdefault(r[8] or "unknown", []).append(r)

    print("\n--- ACCURACY BY EXTRACTION MODEL (GitLab labels as ground truth) ---")
    print(f"{'model':<24} {'records':>7}   {'severity':>12}   {'root cause':>12}   {'service':>12}")
    for model, rs in sorted(by_model.items(), key=lambda kv: -len(kv[1])):
        sev = [(r[4], r[1]) for r in rs if r[4] is not None]
        rc = [(r[5], r[2]) for r in rs if r[5] is not None]
        svc_total = svc_hits = 0
        for r in rs:
            labels = get_service_labels(r[6])
            if labels:
                svc_total += 1
                svc_hits += any(fuzzy_match(r[3], sl) for sl in labels)
        svc = (f"{svc_hits:>3}/{svc_total:<3} {svc_hits / svc_total:>4.0%}"
               if svc_total else "      n/a")
        print(f"{model:<24} {len(rs):>7}   {_acc(sev):>12}   {_acc(rc):>12}   {svc:>12}")
    print("Small samples (under ~30) are noisy - compare models only once "
          "each has a reasonable number of labelled records.")


if __name__ == "__main__":
    run()