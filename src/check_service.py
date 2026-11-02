"""
Checks how many incidents carry a Service::* label - GitLab's own
engineers tagging the actual affected service. If coverage is
decent, this is stronger ground truth for validating the LLM's
affected_service field than spaCy NER (a real human answer beats
a pattern-matching second opinion).

Note: Service::* was NOT parsed into its own raw_incidents column
back in Phase 1 (only severity:: and RootCause:: were) - so this
reads from the full labels_json list instead.

Usage:
    python -m src.check_service_labels
"""

import json
from collections import Counter

from src.db import get_connection


def extract_service_labels(labels: list[str]) -> list[str]:
    return [lbl.split("::", 1)[1] for lbl in labels if lbl.startswith("Service::")]


def run():
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, labels_json FROM raw_incidents"
    ).fetchall()
    conn.close()

    total = len(rows)
    has_service_label = 0
    service_value_counts = Counter()

    # Also check overlap with what's already extracted, since that's
    # what Phase 3 would actually validate against right now
    conn2 = get_connection()
    extracted_ids = set(
        r[0] for r in conn2.execute("SELECT incident_id FROM structured_incidents").fetchall()
    )
    conn2.close()

    has_service_and_extracted = 0

    for incident_id, labels_json in rows:
        labels = json.loads(labels_json) if labels_json else []
        services = extract_service_labels(labels)
        if services:
            has_service_label += 1
            for s in services:
                service_value_counts[s] += 1
            if incident_id in extracted_ids:
                has_service_and_extracted += 1

    print(f"Total incidents: {total}")
    print(f"Have at least one Service::* label: {has_service_label} / {total} "
          f"({has_service_label/total:.1%})")
    print(f"\nOf currently-extracted incidents ({len(extracted_ids)} total), "
          f"how many have a Service::* label:")
    print(f"  {has_service_and_extracted} / {len(extracted_ids)} "
          f"({has_service_and_extracted/len(extracted_ids):.1%})" if extracted_ids else "  N/A - nothing extracted yet")

    print(f"\nTop 20 most common Service::* values:")
    for service, count in service_value_counts.most_common(20):
        print(f"  {service}: {count}")


if __name__ == "__main__":
    run()