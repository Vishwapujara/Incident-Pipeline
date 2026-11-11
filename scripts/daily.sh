#!/bin/bash
# What the launchd job runs each morning (scripts/schedule.sh):
#   1. one refresh cycle (Phase 9)
#   2. export the small dashboard snapshot
#   3. publish it to the dashboard-data branch, which the hosted dashboard reads
#
# The branch is replaced each day by a single commit (force-push), so the
# daily 16 MB snapshot never piles up in the repo's history.

set -uo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python

echo "===== $(date '+%Y-%m-%d %H:%M:%S') daily run ====="
$PY -u -m src.poll || echo "poll exited with an error - publishing what we have"

$PY -m src.export_snapshot || { echo "snapshot export failed"; exit 1; }

blob=$(git hash-object -w snapshot/dashboard.db)
tree=$(printf "100644 blob %s\tdashboard.db\n" "$blob" | git mktree)
commit=$(GIT_AUTHOR_NAME="Vishwa Pujara" GIT_AUTHOR_EMAIL="vishwapujara.08@gmail.com" \
         GIT_COMMITTER_NAME="Vishwa Pujara" GIT_COMMITTER_EMAIL="vishwapujara.08@gmail.com" \
         git commit-tree "$tree" -m "Dashboard data snapshot $(date '+%Y-%m-%d')")
if git push -f origin "${commit}:refs/heads/dashboard-data"; then
  echo "Published snapshot to dashboard-data ($commit)"
else
  echo "Snapshot push failed"; exit 1
fi
