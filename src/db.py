"""
Owns the SQLite schema for this project. Every other script that
needs to read/write the database imports get_connection() from
here rather than opening its own connection with its own idea of
what the schema looks like - one source of truth for the table shape.
"""

import sqlite3
import os

from src.config import DB_PATH


def get_connection():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    # timeout: wait for another process's write (e.g. a running extraction
    # batch) to finish instead of failing immediately with "database is locked".
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema():
    """
    Creates raw_incidents and structured_incidents if they don't
    already exist. Safe to call every time the app starts - CREATE
    TABLE IF NOT EXISTS is a no-op on an already-initialized database.
    """
    conn = get_connection()
    conn.execute("""
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
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS structured_incidents (
            incident_id INTEGER PRIMARY KEY,     -- FK to raw_incidents.id (1-to-1)
            failure_category TEXT NOT NULL,
            severity_assessment INTEGER NOT NULL,
            root_cause_summary TEXT NOT NULL,
            affected_service TEXT,
            llm_model TEXT NOT NULL,             -- which model produced this extraction
            extracted_at TEXT NOT NULL,
            FOREIGN KEY (incident_id) REFERENCES raw_incidents(id)
        )
    """)

    # Migration: if structured_incidents already existed from before
    # llm_model was added, add the column now (safe to call repeatedly -
    # duplicate-column error is caught and ignored).
    try:
        conn.execute(
            "ALTER TABLE structured_incidents ADD COLUMN llm_model TEXT"
        )
    except sqlite3.OperationalError:
        pass  # column already exists

    # Backfill: any existing rows from before this column existed were
    # all extracted with gpt-oss-120b (the only model used so far).
    conn.execute("""
        UPDATE structured_incidents
        SET llm_model = 'openai/gpt-oss-120b'
        WHERE llm_model IS NULL
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS weekly_category_counts (
            week_start TEXT NOT NULL,        -- ISO date, Monday of that week
            failure_category TEXT NOT NULL,
            count INTEGER NOT NULL,
            PRIMARY KEY (week_start, failure_category)
        )
    """)

    # Phase 7: one LLM explanation per flagged finding. finding_key is
    # stable across runs (e.g. "changepoint:Saturation:2026-08-10");
    # evidence_hash changes when the supporting incident set changes,
    # which is what triggers regeneration. active=0 marks findings that
    # no longer appear in the latest run (kept, not deleted, so the
    # history of what was flagged is preserved).
    conn.execute("""
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
        )
    """)

    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_schema()
    print(f"Schema ready at {DB_PATH}")