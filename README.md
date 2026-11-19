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
| 8 | `export_snapshot` + `web/` | Snapshot DB with precomputed detection → Next.js/tRPC dashboard: patterns, trends, search, data coverage | local |
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
                      # (an expired token gets 401, not anonymous access - remove it instead)
DATABASE_URL=...      # optional - Postgres; without it the pipeline uses data/processed/incidents.db
```

## Everyday use

```bash
source .venv/bin/activate
python -m src.poll                    # refresh: new issues, extraction batch, patterns, summaries
python -m src.export_snapshot         # write snapshot/dashboard.db for the dashboard
python -m src.check_status            # how much is extracted
```

Dashboard (Node 22.13+):

```bash
cd web
npm install
npm run dev                           # http://localhost:3000, reads ../snapshot/dashboard.db
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

## Database: SQLite or Postgres

`src/db.py` runs the same SQL on both. Without `DATABASE_URL` it uses the
SQLite file; with it, Postgres (psycopg). Callers use `?` placeholders and
portable upserts (`ON CONFLICT ... DO UPDATE`); the wrapper rewrites the
placeholders for Postgres, so no pipeline module branches on the backend.

```bash
python -m src.migrate_to_postgres     # copy the SQLite DB into DATABASE_URL (keeps paid extractions)
```

## Dashboard architecture

```
Python pipeline ──▶ dashboard_tables ──▶ SQLite / Postgres ──▶ tRPC (Next.js route) ──▶ React UI
 (Groq, spaCy,       (Phase 5 output,                            web/src/server         web/src/components
  HDBSCAN, ruptures)  coverage, config)
```

- **Python owns all analysis.** At the end of each cycle `src.dashboard_tables`
  writes Phase 5's output (`detection_results`, `week_coverage`) and the config
  the UI explains (`dashboard_meta`), so the web app never re-implements a statistic.
- **TypeScript owns the API and UI.** `web/src/server/routers/*` are typed tRPC
  procedures (zod-validated inputs) over either backend: `pg` when
  `DATABASE_URL` is set, otherwise the read-only SQLite snapshot (`node:sqlite`).
  The client uses `@trpc/tanstack-react-query`, so a router change is a compile
  error in the component that uses it.
- `npm run typecheck` / `npm run build` in `web/`; `GET /api/health` checks the DB.

## Docker (local stack)

Two images: `docker/pipeline.Dockerfile` (Python, CPU-only torch, spaCy model
and bge-small embedding model baked in, ~2.8 GB) and `web/Dockerfile` (Next.js
standalone server, ~400 MB).

```bash
docker compose up -d db web                       # Postgres + dashboard on http://localhost:3000
docker compose run --rm pipeline python -m src.migrate_to_postgres --source /seed/incidents.db
docker compose run --rm pipeline                  # one refresh cycle (python -m src.poll)
```

## AWS deployment (Terraform)

`infra/terraform` defines:

| Piece | Resources |
|---|---|
| Network | VPC across 2 AZs; public subnets (ALB, Fargate tasks), private subnets (RDS only, no internet route); no NAT gateway |
| Database | RDS Postgres 16 (encrypted gp3, 7-day backups, `rds.force_ssl`), reachable only from the two ECS task security groups |
| Compute | ECS Fargate on ARM64: dashboard as a service behind an ALB (health check hits `/api/health`, circuit-breaker rollback); pipeline as a task |
| Schedule | EventBridge Scheduler runs the pipeline task daily (09:00 America/New_York) |
| Secrets | Secrets Manager: `DATABASE_URL` (generated) and the Groq key (set out-of-band, so it never enters Terraform state); TLS to RDS verified against the AWS CA bundle in both images |
| Images / seed | ECR repos (scan on push, keep last 10); private S3 bucket for the one-off SQLite seed |

```bash
terraform -chdir=infra/terraform init
terraform -chdir=infra/terraform apply
aws secretsmanager put-secret-value --secret-id "$(terraform -chdir=infra/terraform output -raw groq_secret_arn)" --secret-string "$GROQ_API_KEY"
scripts/aws.sh push      # build ARM64 images, push to ECR, redeploy the dashboard
scripts/aws.sh seed      # load data/processed/incidents.db into RDS via S3 + a one-off task
scripts/aws.sh run       # run a cycle now; `scripts/aws.sh logs` to follow it
terraform -chdir=infra/terraform output dashboard_url
```

Rough cost: ~$45-50/month (ALB, RDS db.t4g.micro, one small Fargate task,
public IPv4 addresses); the daily pipeline task is cents. To tear down, set
`db_deletion_protection = false`, apply, then `terraform destroy`.

## Hosted dashboard without a database (Vercel)

The dashboard also runs with no database: Vercel with **Root Directory =
`web`** and no env vars. Each morning `scripts/daily.sh` exports a trimmed
read-only SQLite copy (`python -m src.export_snapshot`, ~16 MB, report text
cut to 3,000 characters) and force-pushes it as a single commit to the
`dashboard-data` branch, so snapshots don't pile up in history. The server
downloads it to `/tmp` at most once an hour (override with
`DASHBOARD_DATA_URL`). Run locally without `DATABASE_URL`, the dashboard reads
`snapshot/dashboard.db` (or `INCIDENTS_SNAPSHOT_PATH`).

## Data

`data/` is not in git: the raw pull is ~110 MB and the SQLite database
~85 MB. Run the Phase 0–1 bulk pull to rebuild it from scratch:

```bash
python -m src.explore_incident_dataset --full
python -m src.ingest
```
