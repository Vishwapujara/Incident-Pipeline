"""
Small, shared parsing helpers for GitLab's scoped ('::') labels.

Kept separate from ingest.py and explore_label_structure.py so both
can reuse the exact same parsing logic instead of duplicating it -
if GitLab's label format ever needs different handling, it's fixed
in one place.
"""


def extract_severity(labels: list[str]) -> int | None:
    """
    Pulls the numeric severity from a 'severity::N' label, if present.
    Returns None if no severity label exists on this issue (this is
    expected for ~43% of issues, per our exploration).
    """
    for lbl in labels:
        if lbl.startswith("severity::"):
            try:
                return int(lbl.split("::")[1])
            except (IndexError, ValueError):
                return None
    return None


def extract_root_cause(labels: list[str]) -> str | None:
    """
    Pulls the 'RootCause::X' value, if present. Returns the raw
    string (e.g. 'Saturation', 'Software-Change') or None if this
    issue has no RootCause label (~77% of issues, per our exploration).
    """
    for lbl in labels:
        if lbl.startswith("RootCause::"):
            return lbl.split("::", 1)[1]
    return None


def has_label(labels: list[str], exact_name: str) -> bool:
    """Simple exact-match check, e.g. has_label(labels, 'incident')."""
    return exact_name in labels