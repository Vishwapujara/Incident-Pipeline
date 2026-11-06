import os
from dotenv import load_dotenv

load_dotenv()  # reads .env into environment variables

GITLAB_TOKEN = os.getenv("GITLAB_TOKEN")

BASE_URL = "https://gitlab.com/api/v4"
PROJECT_PATH = "gitlab-com%2Fgl-infra%2Fproduction"  # URL-encoded path
# gl-infra/production is a public project, so the token is optional: it
# only raises API rate limits. Leave GITLAB_TOKEN unset/empty to make
# anonymous requests (fine for Phase 9's few-pages-per-poll pulls).
# Note: an INVALID token is not silently ignored - GitLab returns 401.
HEADERS = {"PRIVATE-TOKEN": GITLAB_TOKEN} if GITLAB_TOKEN else {}

RAW_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "raw", "issues_raw.json")
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "processed", "incidents.db")

# --- Phase 2: LLM extraction settings ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Ordered by preference - tried first to last. When one hits its
# daily token limit, extract.py automatically switches to the next
# and keeps going, rather than stopping the whole run.
LLM_MODEL_CHAIN = [
    "openai/gpt-oss-120b",   # strongest reasoning, tried first
    "openai/gpt-oss-20b",    # smaller/faster fallback
    "qwen/qwen3.6-27b",      # second fallback
    "qwen/qwen3.8-27b",      # third fallback
]

# Scoped to the last N years - recent incidents are more relevant to
# "what's recurring right now," and this keeps extraction volume
# feasible given free-tier daily token limits. Centralized here so
# extract.py and later phases (aggregation, dashboard) all use the
# exact same cutoff, not separately-guessed date filters.
from datetime import datetime, timedelta, timezone
DATA_WINDOW_YEARS = 3
DATA_CUTOFF_DATE = (
    datetime.now(timezone.utc) - timedelta(days=365 * DATA_WINDOW_YEARS)
).strftime("%Y-%m-%d")

# Phase 5: how many of the MOST RECENT weeks to exclude from pattern
# detection. Necessary because raw_incidents only contains
# state="closed" issues - incidents from the last 1-2 weeks are
# plausibly still open/unresolved, making recent weeks look
# artificially sparse (right-censoring), not because fewer
# incidents actually happened. Without this exclusion, trend
# detection would likely flag a false "sudden drop" every time it
# runs, since the newest week is always incomplete.
EXCLUDE_RECENT_WEEKS = 2

# Minimum number of (non-excluded) weeks of history a category needs
# before we attempt any pattern detection on it at all - fewer than
# this and a "trend" or "anomaly" would just be noise, not signal.
MIN_WEEKS_FOR_DETECTION = 6

# Minimum TOTAL incidents (across the usable weeks) a category needs
# before a z-score anomaly is trusted. Without this, a category with
# e.g. 1 incident across 8 weeks (7 zeros, one "1") produces a huge
# z-score purely from the math of near-zero variance - that's not a
# real anomaly, it's one incident happening once. Categories below
# this bar are reported as "too sparse to evaluate," not flagged.
MIN_TOTAL_FOR_ZSCORE = 10

# Phase 5: minimum fraction of a week's raw incidents that must have
# been extracted for that week to be used in pattern detection.
# Extraction runs newest-first in daily batches (and can leave holes
# when calls fail), so older weeks are often only partly extracted.
# Without this, the boundary between "partly extracted" and "fully
# extracted" weeks shows up as a false rise in every category at once.
MIN_WEEK_EXTRACTION_COVERAGE = 0.9

# Phase 7: summarization uses a single fixed model, no fallback chain -
# this phase only makes a handful of calls (one per flagged finding,
# not one per incident), so daily rate limits aren't a real concern
# the way they were for Phase 2's bulk extraction.
SUMMARY_MODEL = "openai/gpt-oss-120b"