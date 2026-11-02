# Incident Postmortem Intelligence Pipeline — Project Log

RULES FOR THIS FILE:
- Never delete or edit existing content below.
- Only ever APPEND new entries at the end of the "Log" section.
- Each entry: what was done, what broke (if anything), what caused
  it, how it was fixed, and whether the plan changed as a result.
- Only log genuine technical findings (bugs in the actual pipeline
  logic, data-quality discoveries, design/scoping decisions and
  their reasoning, measured results) - not local copy-paste errors,
  typos, or file-sync mistakes between chat and the local machine.
  Also log anything explicitly requested to be recorded, even if
  it wouldn't otherwise meet this bar.
- [One-time exception, already used: this file's earlier entries
  were edited on 2026-09-13 with explicit permission to remove
  non-technical copy-error entries. The append-only rule is back
  in force as of that edit - no further edits without the same
  explicit permission.]

================================================================
PART 1 — THE PLAN (as finalized, before implementation began)
================================================================

Problem statement:
Software companies write postmortem reports when incidents happen,
but once hundreds accumulate, nobody has time to read back through
them to notice recurring patterns. This pipeline reads all reports,
extracts structured facts, and both (a) makes them searchable and
(b) automatically flags recurring/worsening patterns over time.

Phase 0 — Data acquisition & scoping
  Pull GitLab's public gl-infra/production issue tracker via API,
  check real volume, date range, label taxonomy before building
  anything expensive.

Phase 1 — Raw ingestion + storage schema
  Load the cached raw pull into a proper SQLite raw_incidents
  table. Parse existing severity::N and RootCause::X labels into
  their own columns while preserving the full original label list.

Phase 2 — LLM extraction pipeline
  For every raw incident, use an LLM to extract failure_category,
  severity_assessment, root_cause_summary, affected_service into
  a structured_incidents table. Schema based on GitLab's real
  RootCause::* label vocabulary (discovered in Phase 0), not
  invented categories. Idempotent — only processes incidents
  without an existing structured result.

Phase 3 — Cross-check validation
  Compare LLM extraction against classical NER (spaCy) and against
  GitLab's own existing severity/RootCause labels (used as ground
  truth where present) to measure real precision/recall.

Phase 4 — Aggregation + time series
  Group structured incidents by week x failure_category into
  count time series.

Phase 5 — Pattern detection
  Change-point detection (ruptures) per category, z-score anomaly
  flags, cross-correlation-with-lag between category pairs.

Phase 6 — Semantic clustering
  Embed root_cause_summary text, cluster to catch near-duplicate
  root causes that ended up in different categories.

Phase 7 — Summarization
  LLM generates plain-language explanations for flagged patterns,
  grounded in the linked raw texts.

Phase 8 — Dashboard
  Streamlit: weekly trend charts, flagged patterns + explanations,
  search over raw+structured incidents, drill-down to source.

Phase 9 — Continuous ingestion
  Poll GitLab's API on a schedule for new postmortems. Idempotent:
  only new raw records inserted, LLM extraction runs only on
  records without an existing structured_incidents row.
  Aggregation/pattern-detection re-run on the full table each
  cycle. Dashboard reflects updates automatically on next load.

Phase 10 (optional) — Distillation + orchestration
  Train a lightweight classifier on LLM-labeled data for cheaper
  future extraction. Wrap Phase 9 polling in Prefect.

Tech stack: requests, SQLite, Groq API + Pydantic (extraction),
spaCy (NER cross-check), pandas (aggregation), ruptures
(change-point detection), sentence-transformers + scikit-learn
(clustering), Streamlit (dashboard). VS Code, no Colab needed —
no GPU-dependent steps in the core pipeline.

================================================================
PART 2 — LOG (append new entries below, oldest first)
================================================================

--- Entry: Phase 0 setup ---
Set up project structure in VS Code (src/, data/raw/,
data/processed/). Created config.py, gitlab_client.py,
explore_incident_dataset.py.

Note: GitLab's PROJECT_PATH required URL-encoding the '/'
characters as %2F — mechanical requirement of the API, not a bug.

Fix: added os.makedirs(exist_ok=True) before the raw-data file
write in explore_incident_dataset.py, so the script creates its
own output folder if missing rather than failing. Plan change:
none, just made the script more defensive.

--- Entry: Phase 0 pagination bug ---
Ran explore_incident_dataset.py --full. Result showed "page 1/1"
despite the project clearly having far more issues than 100.
Root cause: GitLab deprecated the X-Total-Pages response header
for large collections (performance reasons); our code defaulted
to total_pages=1 when the header was missing, causing the loop
to stop after page 1 even though more data existed.
Fix: rewrote pagination to check the Link header's rel="next"
presence instead of relying on a total-page count. Re-ran --full:
correctly paged through all 131 pages, 13,017 total issues,
date range 2016-04-15 to 2026-09-09.
Plan change: none to the phases themselves, but this was a real
bug that would have silently capped the entire project's dataset
at 100 records if not caught by manually inspecting the output.

--- Entry: Phase 0 label structure exploration ---
Built explore_label_structure.py to check what fraction of
issues already carry existing severity::N and RootCause::X
labels (rather than assuming everything needs LLM inference).
Findings: 57% have a severity label, 23% have a RootCause label,
53% have both severity label + description >500 chars (a strong
free validation set). Real RootCause::* vocabulary found: 19
concrete categories (Saturation, Software-Change, Config-Change,
Known-Software-Issue, External-Dependency, Indeterminate,
FalseAlarm, Naive-Traffic, DB-Migration, Malicious-Traffic,
Feature-Flag, SPoF, GCP-Networking, Flaky-Test,
Release-Compatibility, Database-Failover, Security, plus
Needed/NotNeeded which are process labels, not root causes).
Plan change: Phase 2's failure_category schema was redesigned to
use this real vocabulary instead of invented placeholder
categories (database/deploy/network/etc). Stage 4 validation
plan upgraded from "hand-label 50-100 records" to "use existing
labels as ground truth for ~53% of the dataset for free, hand-
label only the unlabeled slice."

--- Entry: Phase 1 implementation ---
Built label_utils.py (extract_severity, extract_root_cause parsing
helpers), db.py (SQLite schema + connection helper), ingest.py
(loads issues_raw.json into raw_incidents table).
Ran python -m src.ingest: 13,017 loaded, 13,017 inserted, 13,017
total in table. Clean run, no errors.
No problems encountered. No plan changes.

--- Entry: Phase 2 implementation ---
Built schema.py (Pydantic ExtractionResult using the real
RootCause vocabulary), llm_client.py (Groq API wrapper with
retry-on-malformed-output), extract.py (idempotent extraction
script, LEFT JOIN against structured_incidents to find
unprocessed rows), added structured_incidents table to db.py.

Problem: LLM extraction failed for all 10 test records with
"model llama-3.3-70b-versatile does not exist or you do not have
access to it" (404). Root cause: Groq restructured model tiers;
this model is Enterprise-only with contact-sales access, not
available on this account's Free/Developer tier — documentation
found online disagreed on this, so the account's real model list
was checked directly via the API instead of trusting docs.
Fix: built check_available_models.py to query the account's real
available models. Confirmed list included openai/gpt-oss-120b,
openai/gpt-oss-20b, qwen models, whisper, orpheus, etc. Switched
LLM_MODEL to "openai/gpt-oss-120b" (strongest general-purpose
reasoning model on the confirmed-available list).
Re-ran --limit 10: Succeeded 10, Failed 0. Plan change: none to
phase structure, only to the specific model name used.

Problem (discovered during manual quality check, not a crash):
inspected real extraction output for incident 4521 — LLM
correctly returned "Indeterminate" because the actual raw_text
was almost entirely unfilled GitLab postmortem TEMPLATE
boilerplate (headers followed by "- ..." placeholders, HTML
comments, no real incident content). Root cause: Phase 0's
"description length > 500 chars" proxy for "real postmortem"
is a weak signal — a mostly-empty template with headers and
instructional comments easily exceeds 500 characters without
containing any real information. Some real content may also be
intentionally redacted into separate private linked issues not
reachable via public API. This was not caught earlier because
Phase 0/1 never inspected individual record content closely, only
aggregate statistics (counts, label frequency, length thresholds).
Status: OPEN. Next step decided: measure how widespread this
template-emptiness pattern is across the full 13,017 records
before deciding whether a dedicated is_likely_template flag/filter
is worth building, rather than guessing at the scope of the
problem. This measurement has not been run yet as of this entry.

--- Entry: template-emptiness measurement (resolves prior open item) ---
Built measure_template_emptiness.py: strips GitLab's <!-- --> HTML
comments and unfilled placeholder lines ("- ...", "1. ...") from
every cached raw_text, then measures how much real content is
left, regardless of raw character count.

Result on full 13,017-record dataset:
  - Only 16 / 13,017 (0.1%) are likely mostly-template (real
    content < 200 chars after stripping).
  - Of those 16: 15 still have a severity label, 0 have a
    RootCause label (makes sense - severity gets set quickly,
    root cause requires investigation that never happened for
    these).
  - Average real content across ALL records: 4,637 chars (vs.
    5,825 chars average raw length) - confirms incident 4521 was
    a genuine outlier, not representative of the dataset.

Decision: this is NOT a systemic problem. 0.1% is negligible -
no dedicated is_likely_template flag or filtering logic will be
built. These 16 records will simply flow through normal Phase 2
LLM extraction like everything else; the LLM already correctly
marks them Indeterminate (demonstrated on incident 4521), which
is the correct behavior, not something to work around.
Plan change: none. Phase 2 proceeds exactly as designed, no code
changes required as a result of this investigation.

--- Entry: Phase 2 full run hit daily rate limit, exposed a real bug ---
Ran python -m src.extract on the full 12,960 unprocessed records.
Succeeded cleanly through ~867 records (25-record commits working,
zero failures up to that point - openai/gpt-oss-120b extraction
quality holding up at scale).

Problem: at record ~868, hit Groq's daily token limit (TPD) for
openai/gpt-oss-120b (200,000 TPD cap on this account/tier). Root
cause of the CODE problem (not the rate limit itself, which is
expected/normal): llm_client.py's retry logic retried immediately
3 times with no wait on ANY failure, including rate-limit errors,
then moved to the next record - which hit the identical rate limit
wall and repeated the same pointless retry-fail cycle. This burned
through ~200 more records (868 to 1068+) as guaranteed failures
with no chance of success, producing pure log noise and wasted
time, before the user manually interrupted the run (Ctrl+C).

Fix: added a DailyRateLimitExceeded exception in llm_client.py,
raised immediately (no retry) when Groq's RateLimitError is caught
- since a daily/token-quota rate limit will not resolve within the
same run, retrying is pointless. extract.py now catches this
specific exception, commits all progress made so far, prints a
clear message with the exact record it stopped at, and exits the
run cleanly - rather than continuing to loop through remaining
records that would all fail identically.

Plan change: none to the phases themselves. This is now the
expected, correct behavior for hitting a daily rate limit: the
script stops cleanly and can simply be re-run later (idempotent
LEFT JOIN logic already ensures it picks up exactly where it left
off, skipping the ~867 already-successful records). Full extraction
across all 12,960 records will require multiple runs across
multiple days (or upgrading to Groq's paid Dev tier) given the free
tier's daily token cap. Status: extraction partially complete
(~867-1000ish records done, exact count to be confirmed by querying
structured_incidents directly), remainder pending future runs.

--- Entry: added model fallback chain ---
User requested: automatically switch to a backup model when the
current one exhausts its daily token quota, rather than stopping
the run entirely - and track which model produced each record.

Implemented: LLM_MODEL_CHAIN in config.py, ordered by preference
(openai/gpt-oss-120b -> openai/gpt-oss-20b -> qwen/qwen3.6-27b ->
qwen/qwen3.8-27b), all confirmed available on this account via
check_available_models.py. llm_client.py's extract_incident() now
takes a model parameter instead of a fixed import. extract.py
manages a model_index pointer: on DailyRateLimitExceeded, it
advances to the next model in the chain and retries the SAME
record (not skipping it), only stopping the whole run if every
model in the chain is exhausted. Added llm_model column to
structured_incidents (via migration + backfill of the existing 235
records, all correctly attributed to openai/gpt-oss-120b since
that was the only model used before this change) so every row
records exactly which model produced it - full traceability across
a mixed-model dataset.
Plan change: none to phase structure. This should let extraction
progress much further per run before needing to wait for quota
resets, since 4 independent daily quotas are now available instead
of 1. Status: not yet tested with this new code - next step is to
re-run python -m src.extract and observe whether it switches
models correctly when the first one hits its limit.

--- Entry: fallback chain confirmed working; scoped dataset to 3 years ---
Ran python -m src.extract with the fallback chain. Confirmed
working as designed: succeeded cleanly to record 108, hit
openai/gpt-oss-120b's daily limit, automatically switched to
openai/gpt-oss-20b, kept going without stopping the run.

Noted separately (not a bug, just an observation): one record
(id=367) failed with the model outright REFUSING to answer
("I'm sorry, but I can't help with that") rather than malformed
JSON - likely a content-safety filter triggered by that specific
incident's text (plausibly one describing malicious traffic or a
security exploit in detail, given Malicious-Traffic/Security are
real categories in this dataset). Correctly fell through to
SKIPPED via existing retry logic. Not fixed/investigated further
at this time - noted as a known, low-frequency failure mode.

Decision: scoped the whole remaining Phase 2 extraction (and all
later phases) to the last 3 years of data only, rather than the
full 10-year/13,017-record history. Reasoning: (1) recency is more
relevant to the project's actual purpose - detecting CURRENTLY
recurring patterns, not historical ones from deprecated systems;
(2) feasibility - even with the 4-model fallback chain
(~3,000-3,500 combined records/day), the full 13,017 would take
multiple days, while the 3-year window (4,730 records) is
achievable in roughly 1-1.5 days. Checked actual counts before
deciding: 5-year window = 8,207 records, 3-year window = 4,730
records - went with 3-year as the better feasibility/relevance
tradeoff.

Implementation: added DATA_WINDOW_YEARS=3 and a computed
DATA_CUTOFF_DATE to config.py (centralized so later phases -
aggregation, dashboard - use the exact same cutoff rather than
each guessing their own date filter). extract.py's
get_unprocessed() now filters WHERE created_at >= cutoff, AND
orders by created_at DESC (most recent first) instead of
unordered - ensures that if a run gets interrupted partway, the
MOST relevant (recent) records are prioritized and completed
first, rather than processing older records within the window
before newer ones.

Open item: the ~130+ records already extracted from before this
change (2016-era, outside the 3-year window) remain in
structured_incidents and are NOT deleted - they're simply outside
the window and will be excluded by date filters in later
aggregation phases rather than removed from the database. No
plan change to phase structure, only to the historical depth of
data actually processed.

--- Entry: adopted incremental daily-batch workflow ---
Decision: rather than waiting for the full 3-year (4,730 record)
extraction to complete before starting Phase 3+, adopted a
daily-incremental workflow instead - extraction was blocking all
downstream progress, which isn't necessary given the idempotent
design already in place.

New workflow: run `python -m src.extract --limit 300` (or 400,
etc.) once per day/session to add a bounded batch of the most
recent unprocessed records (ORDER BY created_at DESC from the
last change ensures newest-first). Immediately proceed to build
and exercise Phase 3 (validation) and later phases against
whatever subset currently exists in structured_incidents, rather
than blocking on total volume. Each day, run another bounded
batch and re-run downstream phases against the growing dataset.

No code changes required - --limit and the idempotent LEFT JOIN
skip-logic already fully support this; this is a workflow/process
decision, not an implementation change. Plan change: Phase 3
onward will now be built and iterated against a partial,
progressively growing dataset rather than waiting for Phase 2 to
be "complete" - full 3-year coverage will accumulate over several
days of running small batches alongside continued work on later
phases.

--- Entry: check_status.py built; discovered extraction was mis-scoped ---
Built check_status.py to give an at-a-glance view of pipeline
state (raw vs. structured counts, in-window vs. total, model
breakdown, category distribution, ground-truth coverage) given
the daily-incremental workflow.

Finding: check_status.py revealed Structured/extracted (in
window): 0 despite 608 total records extracted overall - the
day's --limit 300 batch (173 succeeded) had run without the
DATA_CUTOFF_DATE filter and DESC ordering actually applied, so it
processed old, out-of-window records instead of recent ones.

Decision: the 608 already-extracted out-of-window records are
NOT wasted or deleted - they remain valid data, simply outside
the 3-year scope, and will be naturally excluded by date filters
in later phases. No re-extraction of them planned. Next real
progress resumes once the corrected, properly-scoped extract.py
is run against fresh daily quota.

--- Entry: Service::* label coverage check, Phase 3 validation design finalized ---
Built check_service_labels.py to check whether GitLab's Service::*
labels (a separate taxonomy from severity::N and RootCause::X,
never parsed into its own raw_incidents column in Phase 1 - read
directly from labels_json instead) could serve as real ground
truth for validating the LLM's affected_service field, as an
alternative/supplement to a planned spaCy NER cross-check.

Findings:
  - 6,143 / 13,017 (47.2%) of ALL incidents have at least one
    Service::* label - a substantial, real ground-truth source
    (a human engineer's actual answer, not a guess).
  - Of the 608 currently-extracted incidents specifically: only
    72 (11.8%) have a Service::* label - much lower than the
    dataset-wide rate, likely because these 608 are old
    (pre-2023) records from before Service:: labeling practices
    were as consistent (same pattern as severity/RootCause label
    coverage improving over time, observed earlier).
  - Top real service values in use: Gitaly (682), Sidekiq (375),
    Web (369), Postgres (348), Patroni (302), CI Runners (276),
    and 15+ others - confirms this is a genuine, actively-used
    taxonomy, not sparse/abandoned labeling.

Clarification worth recording: Service::* labels and spaCy NER
are fully independent methods - NER reads raw_text directly and
has no knowledge of labels_json, so NER's applicability is NOT
reduced by low Service::* coverage in the current 608-record
batch. NER runs on 100% of records regardless. The coverage
numbers only affect how much WEIGHT each method carries per
record: where Service::* exists, it's the stronger ground-truth
check; where it doesn't (52.8% of all data), NER remains the
only available cross-check, not a redundant one.

Decision: Phase 3's affected_service validation will use BOTH,
not one instead of the other - Service::* as primary ground
truth wherever present, spaCy NER as a secondary/fallback cross-
check everywhere else (and additionally as a grounding sanity-
check even on records WITH ground truth, to catch potential LLM
hallucination of service names not literally present in the raw
text). Plan change: Phase 3 scope is now three-pronged: (1)
severity vs severity_label confusion matrix, (2) failure_category
vs root_cause_label confusion matrix, (3) affected_service vs
Service::* label (where present) + spaCy NER (as fallback and
grounding check) - broader than the original two-track plan.

--- Entry: Phase 3 built and run against the old (out-of-window) extraction batch ---
Built and ran validate.py (Phase 3) against the 608 records
extracted before the 3-year window scoping was correctly applied.
Deliberate choice: rather than wait for fresh in-window extraction
(blocked - the prior day's Groq daily quota was fully exhausted
across all 4 fallback models), proceeded with Phase 3 on the
existing data now, since validation measures EXTRACTION ACCURACY
of the method, not recency - the 3-year window only matters for
later aggregation/trend phases, not for testing whether the LLM's
extraction is trustworthy. This let the project keep moving
forward instead of sitting idle waiting on quota reset.

Results (608 records, all pre-2023):
  - Severity: 43.2% exact-match accuracy (80/185 records with
    ground truth). Confusion matrix revealed a real directional
    bias, not just noise: the LLM systematically UNDERESTIMATES
    the most critical incidents - of true severity-1 incidents,
    it only correctly said "1" in 2/15 cases, mostly guessing 2
    or 3 instead. Middle severities (2,3) get predicted far more
    often than the matrix's true distribution would suggest - the
    model compresses toward the middle rather than committing to
    extremes.
  - Root cause category: NO ground truth available in this batch
    at all (0 records with a root_cause_label) - consistent with
    RootCause:: labeling being a more recent GitLab practice,
    same pattern as Service:: label coverage being much lower in
    this old batch (11.8%) than dataset-wide (47.2%).
  - Affected service: 40.3% match against Service::* ground truth
    (29/72), 37.1% match against spaCy NER fallback (141/380), 156
    records had neither label nor NER entities to check against.
    The near-equal rates between ground-truth and NER checks is a
    hint that some of this gap may be a fuzzy-matching/phrasing
    limitation rather than purely genuine LLM inaccuracy (e.g.
    LLM says "the git storage backend" vs ground truth "Gitaly" -
    semantically right, zero literal token overlap) - flagged as
    an open question, not yet resolved with a manual side-by-side
    sample check.

Now on a new day with Groq's daily quota reset: decided to clear
structured_incidents entirely (DELETE FROM structured_incidents;
raw_incidents left untouched, no need to re-ingest) and restart
extraction correctly scoped to the 3-year window this time
(python -m src.extract --limit 300), since the prior 608 records'
real value (getting Phase 3 built and given an initial real signal)
has already been captured in this log - no further use in keeping
that specific batch of extracted rows once fresh in-window data
starts accumulating. Plan change: none to phase structure or
findings validity - this was a sequencing/workflow decision to
avoid blocking on quota resets, not a correction of an error.

--- Entry: Phase 3 re-run on fresh in-window data; severity accuracy finding is confounded ---
Ran a fresh, correctly-scoped extraction batch (--limit 300,
250 succeeded via the model fallback chain, 0 failed) against the
wiped, properly 3-year-windowed database. Re-ran validate.py
against this batch.

Initial results looked dramatically better than the old batch:
severity 98.8% exact match (165/167), root cause 52.9% (9/17,
first time this check had any ground truth at all), affected
service 75.9% against Service::* ground truth (22/29) but only
27.3% against spaCy NER fallback (56/205).

Investigated the 98.8% severity number rather than taking it at
face value, given how large the jump was from the old batch's
43.2%. Manually inspected 5 random ground-truth records: every
single one had the severity explicitly stated in the raw text's
first line (e.g. "QA smoke jobs can't get started (Severity 3)")
- these are incident.io-generated tickets (a different/newer
incident-management tool than the older manually-templated GitLab
issues), which apparently auto-generate titles that always state
severity outright.

Ran a SQL check across all 167 ground-truth records for how many
have the severity explicitly stated in this exact pattern: 164/167
(98.2%) are confounded this way - only 3 records require genuine
inference. Of those 3, the LLM got only 1 correct (33%).

Conclusion: the 98.8% headline accuracy is REAL but almost
entirely explained by the LLM reading/transcribing an already-
stated value, not by genuine severity judgment. The true
inference-only accuracy (1/3) is a tiny sample but directionally
consistent with the old batch's 43.2% (and its systematic
underestimation-of-critical-incidents bias) - there is currently
NO strong evidence this model can reliably judge severity from
incident details alone when the answer isn't already written out.
This is a significant, well-substantiated finding for the
project's honest self-assessment: extraction accuracy claims must
be checked for this kind of confound (is the model actually
inferring, or just transcribing a stated value) before being
reported as a capability. Plan change: when reporting Phase 3
results going forward (write-up, resume, etc.), the confounded and
unconfounded severity accuracy will be reported separately, not as
one blended number.

--- Entry: Phase 4 built; discovered right-censoring in recent weeks ---
Built aggregate.py (weekly_category_counts table, full recompute
each run - cheap enough not to need incremental logic, unlike
extraction). Ran on the 250-record fresh batch: 69 (week,
category) rows, spanning 2026-07-08 to 2026-09-09 - only ~9 weeks
of history, because --limit 300 pulls the most-recent-first
unprocessed incidents, and GitLab's current incident volume is
dense enough that 250 records doesn't reach far back in time yet.
Minor fix: added .dt.tz_localize(None) before .dt.to_period('W')
to silence a harmless but noisy pandas timezone-drop warning.

Finding: weekly totals were steady (22-36/week) from early July
through Aug 24, then dropped sharply in the last two weeks (Aug 31:
8, Sep 07: 2). Investigated before assuming this is a real trend.
Root cause: likely RIGHT-CENSORING, not a genuine decline -
gitlab_client.py's fetch_issues_page() only pulls state="closed"
issues (by design, since only resolved incidents have full
postmortem text). Incidents from the most recent 1-2 weeks are
plausibly still OPEN/unresolved as of extraction time, so they
don't yet exist in raw_incidents at all - making the most recent
weeks look artificially sparse purely because "not everything
that happened has finished being closed yet," not because fewer
incidents actually occurred.

Decision: Phase 5 (pattern/change-point detection) must exclude
or explicitly flag the most recent 1-2 weeks of any weekly series
before running trend detection - otherwise it would very likely
FALSELY flag a "sudden drop" that is actually just an artifact of
incomplete data, not a real pattern. Plan change: Phase 5's design
now includes this censoring-aware exclusion as a requirement, not
an optional refinement - without it, the phase's core output
(pattern flags) would be actively wrong on real data. Not yet
implemented - noted for when Phase 5 is built.

--- Entry: Phase 5 built; caught a sparse-data false-anomaly issue before trusting it ---
Built detect_patterns.py: per-category z-score anomaly detection
and ruptures change-point detection, reading from
weekly_category_counts. Implemented the right-censoring exclusion
decided earlier (EXCLUDE_RECENT_WEEKS=2, dropped before any
detection runs) and a minimum-history gate (MIN_WEEKS_FOR_DETECTION).

Note: config.py was edited twice for this gate, not once. Initial
value was 8 - chosen as the hard mathematical floor required by
ruptures' Pelt algorithm (min_size=3 per segment, needs 2 segments
minimum = 6) PLUS a small safety margin of 2 extra weeks, since
detecting a change point right at the bare floor of 6 would rest
on very thin evidence (only 3 points per side). Changed to 6
shortly after, on explicit request, to get real results sooner
given how little data currently exists - accepting the tradeoff
that any change-point found right at this threshold carries lower
statistical confidence than it would with the original buffer.

First run (16 categories, 8 usable weeks each after exclusion,
current 250-record batch) surfaced a second, distinct false-signal
problem: several near-empty categories (Database-Failover: 1 total
incident, Feature-Flag: 3, GCP-Networking: 3, Release-Compatibility:
1) were flagged as z-score "ANOMALIES" purely because z-score math
breaks down on near-zero-variance data - a single incident on an
otherwise-all-zero 8-week series produces a large z-score by
construction, not because anything genuinely unusual happened.

Fix: added MIN_TOTAL_FOR_ZSCORE=10 - a category must have at least
10 total incidents across its usable weeks before a z-score result
is trusted; below that, the script reports "too sparse to
evaluate" instead of a misleading flag. Same principle as the
severity-confound catch in Phase 3 - checking WHY a result looks
the way it does before reporting it, rather than trusting a
number at face value.

Result after the fix (same 250-record batch): only categories
with real volume produced results - Saturation (61 total
incidents) showed a genuine CHANGE-POINT around week of 2026-08-10
(the strongest, most trustworthy finding so far in the project);
External-Dependency (13) and Software-Change (12) each showed a
single-week anomaly; Config-Change (32) and Indeterminate (87)
were evaluated with real statistical power and genuinely showed
no notable pattern. All other categories (11 of 16) correctly
reported as too sparse to evaluate given current data volume -
expected to resolve as more --limit extraction batches accumulate
history over coming days. Plan change: none to phase structure -
this was a correctness fix within Phase 5, confirming the same
"verify before trusting" discipline applies at every phase, not
just Phase 3.

--- Entry: Phase 6 built; caught a boilerplate-clustering confound ---
Built cluster_root_causes.py: embeds every root_cause_summary using
bge-small-en-v1.5 (same embedding model as the FilingLens project)
and clusters with HDBSCAN (min_cluster_size=3) - chosen because it
doesn't require pre-specifying the number of clusters, appropriate
since the number of "true" recurring root-cause groups isn't known
ahead of time. Added sentence-transformers and scikit-learn to
requirements.txt (sentence-transformers pulls in torch - a much
heavier install than anything prior in this project).

First run (250 summaries) found 15 clusters, 150 summaries
unclustered (treated as unique). Genuine, valuable findings:
Cluster 3 (8 incidents) - a real recurring root cause (post-deploy
database migration failures: lock timeouts, ACCESS EXCLUSIVE locks,
vacuum contention blocking migrations) scattered across THREE
different failure_category values (DB-Migration, Saturation,
Software-Change) - exactly the "same root cause, inconsistent
categorization" pattern Phase 6 was built to catch. Clusters 0 and
7 were also genuine, tight, single-category recurring patterns
(SSD quota saturation; Sidekiq/PgBouncer saturation).

Problem discovered by reading the actual cluster contents (same
"verify before trusting" discipline as Phase 3/5): clusters 8
through 14 (46 of the ~100 clustered summaries - nearly half)
were NOT grouping by shared root cause at all. They were grouping
because the LLM's BOILERPLATE REFUSAL PHRASING was semantically
similar ("No root cause discernible," "No root cause is
discernible," "planned change... no incident," etc.) - the
embedding model correctly noticed these sentences are worded
alike, but "worded alike" here means "the LLM gave up in a similar
way," not "these incidents share an underlying cause." This is the
same class of confound as the severity-in-title issue (Phase 3)
and the sparse-data z-score issue (Phase 5): a result that LOOKS
like a meaningful finding but is actually an artifact of surface
phrasing, not real content similarity.

Fix: added BOILERPLATE_MARKERS (a list of phrases indicating a
non-answer: "no root cause", "not discernible", "no incident",
"planned change", "no failure") and an is_boilerplate() filter
function. run() now filters these summaries out BEFORE embedding/
clustering, printing how many were removed, so genuine content
clusters aren't buried among "the LLM didn't know" clusters.
Re-run pending as of this entry - expected to remove roughly 46
summaries and leave the real clusters (like Cluster 3) more
clearly visible. Plan change: none to phase structure - another
correctness fix, same pattern as Phase 3 and Phase 5, confirming
this discipline (read the actual output, don't trust a clustering/
accuracy number at face value) needs to be applied at every phase
of this project, not just once.

--- Entry: Phase 6 second confound - HDBSCAN chaining after boilerplate filter ---
Re-ran cluster_root_causes.py with the boilerplate filter applied:
correctly removed 91 summaries (159 substantive ones remained -
more than the estimated 46, meaning the boilerplate patterns were
more common in this dataset than the first-pass read suggested).

New problem, again found by reading actual cluster contents rather
than trusting the cluster count: HDBSCAN produced only 2 clusters -
one containing 119 of the 159 incidents (75%), spanning all 13
categories, clearly not a real single group; and one small, tight,
genuine cluster (4 incidents, SSD quota saturation - the same
group seen as Cluster 0 in the very first run). The 119-incident
cluster is a textbook HDBSCAN "chaining" failure: incident A being
loosely similar to B, and B to C, let the algorithm link A-B-C
into one blob even though A and C share little in common - a
structural clustering-algorithm issue, not a data-quality issue
like the previous two confounds.

Root cause identified: (1) HDBSCAN was run with metric="euclidean"
on RAW (non-unit-normalized) sentence embeddings - bge-small and
similar sentence-transformer models are designed to be compared by
COSINE similarity (direction), not raw euclidean distance, so
distances were distorted; (2) HDBSCAN's default
cluster_selection_method="eom" favors fewer, larger clusters,
compounding the chaining tendency.

Fix: added sklearn.preprocessing.normalize() to L2-normalize
embeddings before clustering (makes euclidean distance behave
equivalently to cosine similarity for unit vectors - standard
practice for sentence-embedding clustering), and changed
cluster_selection_method to "leaf" (favors more, smaller, tighter
clusters instead of few large ones - directly counters chaining).
Re-run pending as of this entry to confirm the fix actually breaks
up the 119-incident blob into smaller, genuinely coherent groups;
if chaining persists, next tuning step would be adjusting
min_samples separately from min_cluster_size. Plan change: none to
phase structure - this is the THIRD distinct confound caught
across Phases 3, 5, and 6 by reading actual output rather than
trusting a summary number/count, reinforcing this as a load-bearing
practice for this project, not a one-off habit.