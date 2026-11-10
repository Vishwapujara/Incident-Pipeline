"""
The fixed extraction schema every LLM response must validate against.

failure_category uses GitLab's REAL RootCause::* vocabulary discovered
during Phase 0 exploration (explore_label_structure.py output) -
not invented categories. 'Other' is included as an escape hatch for
genuine edge cases the LLM can't confidently place.

Kept as its own file (not inside extract.py) because Stage 4 validation
and any future re-use of this schema should import the exact same
definition - one source of truth for what "correct shape" means.
"""

from typing import Optional, Literal
from pydantic import BaseModel, Field

FailureCategory = Literal[
    "Saturation",
    "Software-Change",
    "Config-Change",
    "Known-Software-Issue",
    "External-Dependency",
    "Indeterminate",
    "FalseAlarm",
    "Naive-Traffic",
    "DB-Migration",
    "Malicious-Traffic",
    "Feature-Flag",
    "SPoF",
    "GCP-Networking",
    "Flaky-Test",
    "Release-Compatibility",
    "Database-Failover",
    "Security",
    "Other",
]


class ExtractionResult(BaseModel):
    failure_category: FailureCategory = Field(
        description="Best-matching root cause category for this incident."
    )
    severity_assessment: int = Field(
        ge=1, le=4,
        description="LLM's own severity judgment, 1 (most severe) to 4 (least severe), "
                    "matching GitLab's own severity::N scale."
    )
    root_cause_summary: str = Field(
        description="1-2 sentence plain-language summary of the actual root cause, "
                    "in the LLM's own words."
    )
    affected_service: Optional[str] = Field(
        default=None,
        description="Name of the primary service/system affected, if identifiable."
    )
    # Added 2026-11-10. Replaces guessing from the summary's wording
    # (cluster_root_causes.BOILERPLATE_MARKERS) with an explicit answer.
    # Optional so a model that omits it doesn't fail validation - those
    # records fall back to the phrase markers, like pre-existing rows.
    root_cause_identified: Optional[bool] = Field(
        default=None,
        description="True if the report states or clearly implies a concrete root "
                    "cause; False if it is unknown, not yet investigated, or the "
                    "issue was not a failure (planned change, false alarm)."
    )