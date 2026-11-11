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
DB_PATH = os.getenv("INCIDENTS_DB_PATH") or os.path.join(
    os.path.dirname(__file__), "..", "data", "processed", "incidents.db")

# --- Phase 2: LLM extraction settings ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Ordered by preference - tried first to last. When one hits its
# daily token limit, extract.py automatically switches to the next
# and keeps going, rather than stopping the whole run.
LLM_MODEL_CHAIN = [
    "openai/gpt-oss-120b",   # strongest reasoning, tried first
    "openai/gpt-oss-20b",    # smaller/faster fallback
    "qwen/qwen3.8-27b",      # second fallback
    # qwen/qwen3.6-27b removed 2026-11-06: Groq retired it (404
    # model_not_found) - see PROJECT_LOG.md.
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
# Raised 2 -> 4 on 2026-11-06 after MEASURING the lag (Phase 9's first
# incremental pull): of incidents created 0/1/2/3 weeks before a pull,
# 95%/62%/8%/17% were still open at pull time; from 4 weeks back it's
# mostly 0-6%. With 2, weeks 2-3 were still undercounted by up to 17%.
EXCLUDE_RECENT_WEEKS = 4

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
# Phase 5: detection runs on VOLUME-ADJUSTED counts. Each category's
# expected count in a week = that week's total incidents x the category's
# overall share; the score is the Pearson residual (count - expected) /
# sqrt(expected), which is ~unit-variance if the category simply tracks
# total volume. Added 2026-11-10: raw-count detection flagged the same
# dates (Apr 20, May 25, Sep 7) in 6 categories at once, because total
# volume rose 49 -> 89/week and then fell to 22 - one volume shift
# reported as many "category trends" (see PROJECT_LOG.md).
#
# Unusual week: |residual| >= this. 3 rather than 2 because ~11 categories
# x ~30 weeks is ~330 tests; at 2, roughly 8 would fire by chance alone.
ANOMALY_RESIDUAL_THRESHOLD = 3.0
# Change-point penalty = this x log(weeks), on the unit-variance residuals
# (a BIC-style penalty - scale-free, unlike the old fixed pen=3 on counts).
CHANGEPOINT_PENALTY_FACTOR = 2.0
