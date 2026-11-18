"""
One-off: copy the SQLite working database into Postgres (DATABASE_URL).

The 13k raw incidents can be re-pulled from GitLab, but the extractions
and summaries cost LLM tokens - this carries them over instead of
re-running Phases 2 and 7 against the new database.

The source can be a local path or an s3:// URL (how the AWS deployment
seeds RDS, which is only reachable from inside the VPC: upload the
SQLite file to the seed bucket, then run this as a one-off ECS task).

Usage:
    DATABASE_URL=postgresql://... python -m src.migrate_to_postgres
    python -m src.migrate_to_postgres --source s3://bucket/incidents.db --replace
"""

import argparse
import os
import sqlite3
import tempfile

from src.config import DB_PATH
from src.db import BACKEND, TABLES, get_connection, init_schema

BATCH = 1000


def _local_copy(source):
    if not source.startswith("s3://"):
        return source
    import boto3
    bucket, key = source[5:].split("/", 1)
    dest = os.path.join(tempfile.gettempdir(), "seed_incidents.db")
    print(f"Downloading {source} ...")
    boto3.client("s3").download_file(bucket, key, dest)
    return dest


def run(source, replace=False):
    if BACKEND != "postgres":
        raise SystemExit("Set DATABASE_URL to the target Postgres database.")
    path = _local_copy(source)
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    have = {r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}

    init_schema()
    dst = get_connection()
    existing = dst.execute("SELECT COUNT(*) FROM raw_incidents").fetchone()[0]
    if existing and not replace:
        raise SystemExit(f"Target already has {existing} raw incidents; pass --replace to overwrite.")
    if replace:
        # Children first (structured_incidents references raw_incidents).
        for table in reversed(list(TABLES)):
            dst.execute(f"DELETE FROM {table}")

    for table in TABLES:  # dict order = parents before children
        if table not in have:
            print(f"  {table}: not in source, skipped")
            continue
        cols = [c[0] for c in dst.execute(f"SELECT * FROM {table} LIMIT 0").description]
        src_cols = {r[1] for r in src.execute(f"PRAGMA table_info({table})")}
        cols = [c for c in cols if c in src_cols]
        sql = (f"INSERT INTO {table} ({', '.join(cols)}) "
               f"VALUES ({', '.join('?' * len(cols))})")
        cur = src.execute(f"SELECT {', '.join(cols)} FROM {table}")
        n = 0
        while rows := cur.fetchmany(BATCH):
            dst.executemany(sql, rows)
            n += len(rows)
        print(f"  {table}: {n:,} rows")
    dst.commit()
    dst.close()
    src.close()
    print("Done.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    p.add_argument("--source", default=DB_PATH, help="SQLite file path or s3:// URL")
    p.add_argument("--replace", action="store_true", help="empty the target tables first")
    a = p.parse_args()
    run(a.source, a.replace)
