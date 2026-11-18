"""
Owns the database schema and connections for this project. Every other
script imports get_connection() from here rather than opening its own
connection - one source of truth for the table shape AND the backend.

Two backends, same SQL:
  - SQLite (default): data/processed/incidents.db, for local work.
  - Postgres: when DATABASE_URL is set (docker-compose, AWS RDS).

Callers write plain DB-API SQL with `?` placeholders and portable syntax
(ON CONFLICT ... DO UPDATE, not INSERT OR REPLACE). For Postgres the
connection wrapper rewrites `?` to `%s`; nothing else differs, so the
pipeline code doesn't branch on the backend.
"""

import os
import re
import sqlite3

from src.config import DATABASE_URL, DB_PATH

BACKEND = "postgres" if DATABASE_URL else "sqlite"

if BACKEND == "postgres":
    import warnings
    # pandas.read_sql_query works through any DB-API cursor; it just only
    # *tests* sqlite3 and SQLAlchemy, and says so on every call.
    warnings.filterwarnings("ignore", message="pandas only supports SQLAlchemy")

# `?` outside single-quoted string literals.
_PLACEHOLDER = re.compile(r"'[^']*'|\?")


def _to_pyformat(sql):
    # Literal % must be doubled for psycopg's %s paramstyle.
    sql = sql.replace("%", "%%")
    return _PLACEHOLDER.sub(lambda m: "%s" if m.group(0) == "?" else m.group(0), sql)


class _PgCursor:
    """DB-API cursor that accepts sqlite-style `?` placeholders."""

    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql, params=None):
        # psycopg only interprets %-placeholders when params are given.
        if params is None:
            self._cur.execute(sql)
        else:
            self._cur.execute(_to_pyformat(sql), tuple(params))
        return self

    def executemany(self, sql, seq):
        self._cur.executemany(_to_pyformat(sql), [tuple(p) for p in seq])
        return self

    def __getattr__(self, name):  # fetchone, fetchall, description, close, ...
        return getattr(self._cur, name)

    def __iter__(self):
        return iter(self._cur)


class _PgConnection:
    """
    The subset of sqlite3.Connection the pipeline uses (execute,
    executemany, cursor, commit, close), backed by psycopg. pandas'
    read_sql_query works through cursor().
    """

    def __init__(self, conn):
        self._conn = conn

    def cursor(self):
        return _PgCursor(self._conn.cursor())

    def execute(self, sql, params=None):
        return self.cursor().execute(sql, params)

    def executemany(self, sql, seq):
        return self.cursor().executemany(sql, seq)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def get_connection():
    if BACKEND == "postgres":
        import psycopg  # only needed in Postgres mode
        return _PgConnection(psycopg.connect(DATABASE_URL))
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    # timeout: wait for another process's write (e.g. a running extraction
    # batch) to finish instead of failing immediately with "database is locked".
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


TABLES = {
    "raw_incidents": """
        CREATE TABLE IF NOT EXISTS raw_incidents (
            id INTEGER PRIMARY KEY,        -- GitLab issue iid, our natural key
            created_at TEXT NOT NULL,       -- ISO timestamp, kept as text for simplicity
            title TEXT,
            raw_text TEXT,                  -- the full issue description (unstructured source)
            source_url TEXT,
            labels_json TEXT,                -- full original labels list, preserved as JSON
            severity_label INTEGER,          -- parsed from severity::N, NULL if absent
            root_cause_label TEXT,           -- parsed from RootCause::X, NULL if absent
            ingested_at TEXT NOT NULL         -- when THIS pipeline first saw this record
        )""",
    "structured_incidents": """
        CREATE TABLE IF NOT EXISTS structured_incidents (
            incident_id INTEGER PRIMARY KEY,     -- FK to raw_incidents.id (1-to-1)
            failure_category TEXT NOT NULL,
            severity_assessment INTEGER NOT NULL,
            root_cause_summary TEXT NOT NULL,
            affected_service TEXT,
            llm_model TEXT NOT NULL,             -- which model produced this extraction
            extracted_at TEXT NOT NULL,
            root_cause_identified INTEGER,       -- added 2026-11-10; NULL for older rows
            FOREIGN KEY (incident_id) REFERENCES raw_incidents(id)
        )""",
    "weekly_category_counts": """
        CREATE TABLE IF NOT EXISTS weekly_category_counts (
            week_start TEXT NOT NULL,        -- ISO date, Monday of that week
            failure_category TEXT NOT NULL,
            count INTEGER NOT NULL,
            PRIMARY KEY (week_start, failure_category)
        )""",
    # Phase 7: one LLM explanation per flagged finding. finding_key is
    # stable across runs (e.g. "changepoint:Saturation:2026-08-10");
    # evidence_hash changes when the supporting incident set changes,
    # which is what triggers regeneration. active=0 marks findings that
    # no longer appear in the latest run (kept, not deleted, so the
    # history of what was flagged is preserved).
    "pattern_summaries": """
        CREATE TABLE IF NOT EXISTS pattern_summaries (
            finding_key TEXT PRIMARY KEY,
            finding_type TEXT NOT NULL,          -- changepoint | anomaly | cluster
            category TEXT,                       -- NULL for clusters
            stats_json TEXT NOT NULL,            -- numbers the finding is based on
            evidence_ids_json TEXT NOT NULL,     -- incident ids shown to the LLM
            evidence_hash TEXT NOT NULL,
            headline TEXT NOT NULL,
            explanation TEXT NOT NULL,
            cited_ids_json TEXT NOT NULL,        -- subset of evidence ids the LLM cited
            coherent INTEGER NOT NULL,           -- LLM's verdict: is this a real pattern?
            confidence TEXT NOT NULL,            -- high | medium | low
            caveats TEXT,
            llm_model TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        )""",
    # Phase 9: small key/value store for pipeline bookkeeping
    # (e.g. last_poll_started_at for incremental GitLab pulls).
    "pipeline_state": """
        CREATE TABLE IF NOT EXISTS pipeline_state (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""",
    # Dashboard tables (written by src.dashboard_tables after each cycle):
    # Phase 5's output, precomputed so the TypeScript app never
    # re-derives a statistic.
    "detection_results": """
        CREATE TABLE IF NOT EXISTS detection_results (
            series TEXT PRIMARY KEY,          -- failure_category, or '__total__'
            status TEXT NOT NULL,             -- evaluated | too_sparse | insufficient_weeks
            total INTEGER NOT NULL,           -- incidents in analysed weeks
            usable_weeks INTEGER NOT NULL,
            baseline DOUBLE PRECISION,                    -- total series only: typical week
            analysed_weeks_json TEXT NOT NULL,
            anomalies_json TEXT NOT NULL,     -- [{week, count, score, expected}]
            changepoints_json TEXT NOT NULL   -- [week, ...]
        )""",
    "week_coverage": """
        CREATE TABLE IF NOT EXISTS week_coverage (
            week_start TEXT PRIMARY KEY,
            raw INTEGER NOT NULL,
            extracted INTEGER NOT NULL,
            coverage DOUBLE PRECISION NOT NULL,
            used_for_detection INTEGER NOT NULL
        )""",
    "dashboard_meta": """
        CREATE TABLE IF NOT EXISTS dashboard_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )""",
}

# Columns added after a table first shipped: (table, column, type).
_ADDED_COLUMNS = [
    ("structured_incidents", "llm_model", "TEXT"),
    ("structured_incidents", "root_cause_identified", "INTEGER"),
]


def _add_column(conn, table, column, ctype):
    if BACKEND == "postgres":
        conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {ctype}")
        return
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ctype}")
    except sqlite3.OperationalError:
        pass  # column already exists


def init_schema():
    """
    Creates every table if it doesn't already exist. Safe to call every
    time the app starts - CREATE TABLE IF NOT EXISTS is a no-op on an
    already-initialized database.
    """
    conn = get_connection()
    for ddl in TABLES.values():
        conn.execute(ddl)
    for table, column, ctype in _ADDED_COLUMNS:
        _add_column(conn, table, column, ctype)

    # Backfill: any existing rows from before llm_model existed were all
    # extracted with gpt-oss-120b (the only model used so far).
    conn.execute("""
        UPDATE structured_incidents
        SET llm_model = 'openai/gpt-oss-120b'
        WHERE llm_model IS NULL
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_created ON raw_incidents(created_at)")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_schema()
    print(f"Schema ready ({BACKEND}: {DB_PATH if BACKEND == 'sqlite' else 'DATABASE_URL'})")
