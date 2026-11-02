"""
Thin wrapper around the Groq API for structured extraction.

Isolated in its own file for the same reason gitlab_client.py is
separate from explore_incident_dataset.py: extract.py should call
extract_incident() without needing to know about prompt text,
JSON parsing, or retry mechanics.
"""

import json
from groq import Groq, RateLimitError
from pydantic import ValidationError

from src.config import GROQ_API_KEY
from src.schema import ExtractionResult, FailureCategory


class DailyRateLimitExceeded(Exception):
    """
    Raised when the LLM provider's rate limit is hit. Signals the
    caller (extract.py) to stop the whole batch run immediately
    rather than retrying per-record - once this limit is hit, every
    subsequent call will fail identically until the quota resets,
    so continuing wastes time and produces useless log noise.
    """
    pass

if not GROQ_API_KEY:
    raise RuntimeError(
        "GROQ_API_KEY not found. Add it to your .env file "
        "(get a free key at console.groq.com/keys)."
    )

client = Groq(api_key=GROQ_API_KEY)

CATEGORY_LIST = ", ".join(FailureCategory.__args__)

SYSTEM_PROMPT = f"""You are analyzing a software incident postmortem report.
Extract the following fields and respond with ONLY a JSON object, no other text:

{{
  "failure_category": one of [{CATEGORY_LIST}],
  "severity_assessment": integer 1-4 (1 = most severe, 4 = least severe),
  "root_cause_summary": a 1-2 sentence plain-language summary of the root cause,
  "affected_service": the name of the primary affected service, or null if unclear
}}

Base your answer only on the text provided. If uncertain about the category,
use "Indeterminate". If no root cause is discernible, say so plainly in the summary."""


def _call_llm(raw_text: str, model: str) -> dict:
    response = client.chat.completions.create(
        model=model,
        temperature=0.1,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": raw_text[:6000]},  # cap input length
        ],
    )
    return json.loads(response.choices[0].message.content)


def extract_incident(raw_text: str, model: str, max_retries: int = 2) -> ExtractionResult | None:
    """
    Calls the LLM (using the given model) and validates its response
    against ExtractionResult. Retries once on malformed/invalid output.
    Returns None (never raises for ordinary failures) if extraction
    fails after all retries - the caller decides how to handle a
    skipped record.

    Raises DailyRateLimitExceeded immediately (no retry) if this
    model's rate limit is hit - this is NOT a per-record problem,
    so the caller should switch models rather than retry uselessly.
    """
    if not raw_text or not raw_text.strip():
        return None

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            raw_response = _call_llm(raw_text, model)
            return ExtractionResult.model_validate(raw_response)
        except RateLimitError as e:
            raise DailyRateLimitExceeded(str(e))
        except (ValidationError, json.JSONDecodeError, Exception) as e:
            last_error = e
            continue

    print(f"  Extraction failed after {max_retries + 1} attempts: {last_error}")
    return None