# Incident Postmortem Intelligence Pipeline

Reads GitLab's public production incident tracker
([gitlab-com/gl-infra/production](https://gitlab.com/gitlab-com/gl-infra/production/-/issues)),
extracts structured facts from each incident with an LLM, and flags
recurring or worsening failure patterns that nobody has time to spot by
reading hundreds of postmortems.

The full design, every bug found along the way, and the reasoning behind
each decision are in [`src/PROJECT_LOG.md`](src/PROJECT_LOG.md).

## Pipeline

| Phase | Script | What it does | Uses |
|---|---|---|---|
| 0–1 | `explore_incident_dataset`, `ingest` | One-off bulk pull of all closed issues into SQLite | GitLab API |
| 2 | `extract` | LLM extraction: failure category, severity, root-cause summary, affected service | Groq tokens |
| 3 | `validate` | Checks the extraction against GitLab's own labels and spaCy NER | local |
| 4 | `aggregate` | Weekly incident counts per category | local |
| 5 | `detect_patterns` | Change-points and unusual weeks, with censoring corrections | local |
| 6 | `cluster_root_causes` | Groups incidents with the same root cause across categories | local |
| 7 | `summarize` | Plain-language, evidence-cited explanation of each flagged pattern | Groq tokens (one call per finding) |
| 8 | `dashboard` | Streamlit view: patterns, trends, search, data coverage | local |
| 9 | `poll` | One refresh cycle: pull new issues → extract → aggregate → summarize | GitLab API + Groq |

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm
```

Create `.env` in the project root:

```
GROQ_API_KEY=...      # required for extraction/summaries - console.groq.com/keys
GITLAB_TOKEN=...      # optional - the project is public; a token only raises rate limits
```

## Everyday use

```bash
source .venv/bin/activate
python -m src.poll                    # refresh: new issues, extraction batch, patterns, summaries
streamlit run src/dashboard.py        # open the dashboard
python -m src.check_status            # how much is extracted
```

Automatic daily refresh (macOS launchd):

```bash
scripts/schedule.sh on 09:00   # run poll every day at 09:00 (runs on wake if asleep)
scripts/schedule.sh off        # turn it off
scripts/schedule.sh status     # on/off, time, last log lines
scripts/schedule.sh run        # trigger one run now
```

Log: `data/logs/poll.log`. The job runs `scripts/daily.sh`, which also
publishes the hosted dashboard's data (below).

## Hosted dashboard

The dashboard is deployed on Streamlit Community Cloud from
`dashboard/streamlit_app.py`. That folder has its own small
`requirements.txt`, so Cloud doesn't install torch or spaCy.

The working database isn't in git. Each morning `scripts/daily.sh`
exports a trimmed read-only copy (`python -m src.export_snapshot`,
~16 MB, report text cut to 3,000 characters) and force-pushes it as a
single commit to the `dashboard-data` branch, so the snapshots don't pile
up in history. The hosted app downloads it at most once an hour. Run
locally, the dashboard reads `data/processed/incidents.db` directly.

Useful flags:

- `python -m src.extract --limit 300`: extract a bounded batch (newest incidents first)
- `python -m src.summarize --dry-run`: list flagged findings without spending tokens
- `python -m src.summarize --force`: regenerate every summary
- `python -m src.poll --no-extract --no-summarize`: only pull from GitLab

Free-tier Groq limits allow roughly 200 extractions a day on the main
model. When a model hits its daily limit, the scripts fall back through
`LLM_MODEL_CHAIN` in `src/config.py`; if every model is limited, they stop
and keep their progress. Re-running is always safe: finished work is
skipped.

## Data

`data/` is not in git: the raw pull is ~110 MB and the SQLite database
~85 MB. Run the Phase 0–1 bulk pull to rebuild it from scratch:

```bash
python -m src.explore_incident_dataset --full
python -m src.ingest
```
