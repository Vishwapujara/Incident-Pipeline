"""
Measures the scope of the "unfilled template" problem found while
manually inspecting incident 4521: some postmortems pass the
length>500 proxy from Phase 0 purely because of unfilled template
headers, HTML comments, and instructional text - not real content.

This strips out the boilerplate and measures what REAL content is
actually left, across the full cached dataset - so we can decide
whether this is a rare edge case or common enough to need a
dedicated filter/flag, before touching Phase 2's extraction logic.

Usage:
    python -m src.measure_template_emptiness
"""

import re

from src.db import get_connection

# Matches GitLab's <!-- comment --> blocks (can span multiple lines)
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

# Matches lines that are JUST an unfilled placeholder, e.g. "- ...",
# "1. ...", "    1. ..." - real content is never just three dots.
PLACEHOLDER_LINE_RE = re.compile(r"^\s*[-\d.]*\s*\.\.\.\s*$", re.MULTILINE)


def real_content_length(raw_text: str) -> int:
    """
    Strips HTML comments and placeholder-only lines, then returns
    the length of whatever text is actually left. A low number
    here means "mostly template," regardless of raw character count.
    """
    if not raw_text:
        return 0
    cleaned = HTML_COMMENT_RE.sub("", raw_text)
    cleaned = PLACEHOLDER_LINE_RE.sub("", cleaned)
    # Collapse leftover blank lines/whitespace so we're measuring
    # actual content, not empty structure
    cleaned = re.sub(r"\n\s*\n", "\n", cleaned).strip()
    return len(cleaned)


def run():
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, raw_text, severity_label, root_cause_label FROM raw_incidents"
    ).fetchall()
    conn.close()

    total = len(rows)
    print(f"Analyzing {total} cached incidents...\n")

    REAL_CONTENT_THRESHOLD = 200  # chars of real content below which we call it "mostly template"

    likely_template_count = 0
    likely_template_with_severity = 0
    likely_template_with_rootcause = 0
    raw_lengths = []
    real_lengths = []

    for _id, raw_text, severity, root_cause in rows:
        raw_len = len(raw_text or "")
        real_len = real_content_length(raw_text)
        raw_lengths.append(raw_len)
        real_lengths.append(real_len)

        if raw_len > 500 and real_len < REAL_CONTENT_THRESHOLD:
            likely_template_count += 1
            if severity is not None:
                likely_template_with_severity += 1
            if root_cause is not None:
                likely_template_with_rootcause += 1

    print(f"Total incidents: {total}")
    print(f"Raw text > 500 chars (old Phase 0 proxy): "
          f"{sum(1 for l in raw_lengths if l > 500)} / {total}")
    print(f"\nOf those, likely MOSTLY-TEMPLATE "
          f"(real content < {REAL_CONTENT_THRESHOLD} chars after "
          f"stripping comments/placeholders):")
    print(f"  {likely_template_count} / {total} "
          f"({likely_template_count/total:.1%})")

    print(f"\nOf the likely-template records:")
    print(f"  ...still have a severity label anyway:   "
          f"{likely_template_with_severity} / {likely_template_count or 1}")
    print(f"  ...still have a RootCause label anyway:  "
          f"{likely_template_with_rootcause} / {likely_template_count or 1}")
    print("  -> if these are high, the LABELS are still trustworthy")
    print("     ground truth even when the TEXT is empty - useful to know.")

    avg_raw = sum(raw_lengths) / total
    avg_real = sum(real_lengths) / total
    print(f"\nAverage raw length: {avg_raw:.0f} chars")
    print(f"Average real content length (after stripping): {avg_real:.0f} chars")


if __name__ == "__main__":
    run()