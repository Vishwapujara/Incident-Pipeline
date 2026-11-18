import "server-only";

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import pg from "pg";

/**
 * Data access for the tRPC routers. Two backends, one interface:
 *
 *  - Postgres, when DATABASE_URL is set (docker-compose, AWS ECS + RDS).
 *    The pipeline writes the same tables there, including the
 *    precomputed dashboard tables (src/dashboard_tables.py).
 *  - SQLite snapshot otherwise (`python -m src.export_snapshot`):
 *    ../snapshot/dashboard.db locally, or downloaded from the
 *    dashboard-data branch when hosted without a database (Vercel).
 *
 * Routers write SQL with `?` placeholders and portable syntax; `like`
 * gives the case-insensitive operator for the active backend.
 */

type Param = string | number | null;

export interface Db {
  readonly kind: "postgres" | "sqlite";
  /** Case-insensitive LIKE (SQLite's LIKE already is, for ASCII). */
  readonly like: "LIKE" | "ILIKE";
  all<T>(sql: string, ...params: Param[]): Promise<T[]>;
  get<T>(sql: string, ...params: Param[]): Promise<T | undefined>;
}

// ------------------------------------------------------------- postgres

let pool: pg.Pool | null = null;

function pgSsl(): pg.PoolConfig["ssl"] {
  // RDS: verify the server certificate against the AWS CA bundle baked
  // into the image (see web/Dockerfile).
  const ca = process.env.DB_SSL_CA_FILE;
  return ca ? { ca: fs.readFileSync(/*turbopackIgnore: true*/ ca, "utf8"), rejectUnauthorized: true } : undefined;
}

/** `?` → `$1, $2, ...`, skipping quoted literals. */
function toNumbered(sql: string): string {
  let i = 0;
  return sql.replace(/'[^']*'|\?/g, (m) => (m === "?" ? `$${++i}` : m));
}

function postgresDb(url: string): Db {
  pool ??= new pg.Pool({ connectionString: url, ssl: pgSsl(), max: 5, idleTimeoutMillis: 30_000 });
  const p = pool;
  return {
    kind: "postgres",
    like: "ILIKE",
    async all<T>(sql: string, ...params: Param[]) {
      return (await p.query(toNumbered(sql), params)).rows as T[];
    },
    async get<T>(sql: string, ...params: Param[]) {
      return (await p.query(toNumbered(sql), params)).rows[0] as T | undefined;
    },
  };
}

// --------------------------------------------------------------- sqlite

const SNAPSHOT_URL =
  process.env.DASHBOARD_DATA_URL ??
  "https://raw.githubusercontent.com/Vishwapujara/Incident-Pipeline/dashboard-data/dashboard.db";
const LOCAL_PATH =
  process.env.INCIDENTS_SNAPSHOT_PATH ??
  path.join(/*turbopackIgnore: true*/ process.cwd(), "..", "snapshot", "dashboard.db");
const DOWNLOAD_PATH = path.join(os.tmpdir(), "incident_dashboard.db");
const DOWNLOAD_TTL_MS = 60 * 60 * 1000;

type Open = { db: DatabaseSync; file: string; mtimeMs: number; checkedAt: number };
let current: Open | null = null;
let inflight: Promise<void> | null = null;

async function download(): Promise<void> {
  const res = await fetch(SNAPSHOT_URL, { cache: "no-store" });
  if (!res.ok) throw new Error(`Snapshot download failed: ${res.status} ${res.statusText}`);
  const tmp = `${DOWNLOAD_PATH}.${process.pid}.part`;
  fs.writeFileSync(/*turbopackIgnore: true*/ tmp, Buffer.from(await res.arrayBuffer()));
  fs.renameSync(/*turbopackIgnore: true*/ tmp, DOWNLOAD_PATH); // atomic: never open a half-written file
}

/** Returns an open read-only snapshot, reopening when the file changed. */
async function openSnapshot(): Promise<DatabaseSync> {
  const local = fs.existsSync(/*turbopackIgnore: true*/ LOCAL_PATH);
  const file = local ? LOCAL_PATH : DOWNLOAD_PATH;

  if (!local) {
    const stale =
      !fs.existsSync(/*turbopackIgnore: true*/ DOWNLOAD_PATH) ||
      Date.now() - fs.statSync(/*turbopackIgnore: true*/ DOWNLOAD_PATH).mtimeMs > DOWNLOAD_TTL_MS;
    if (stale) {
      inflight ??= download().finally(() => (inflight = null));
      try {
        await inflight;
      } catch (err) {
        // Serve yesterday's copy rather than nothing, if we have one.
        if (!fs.existsSync(/*turbopackIgnore: true*/ DOWNLOAD_PATH)) throw err;
        console.error(err);
      }
    }
  }

  // Cheap mtime check (at most every 5 s) picks up a re-exported snapshot.
  if (current && current.file === file && Date.now() - current.checkedAt < 5000) {
    return current.db;
  }
  const mtimeMs = fs.statSync(/*turbopackIgnore: true*/ file).mtimeMs;
  if (current && current.file === file && current.mtimeMs === mtimeMs) {
    current.checkedAt = Date.now();
    return current.db;
  }
  // Close the old handle later, not now: a request that already holds it
  // may still be between its await and its query.
  const old = current?.db;
  if (old) setTimeout(() => old.close(), 30_000).unref();
  current = {
    db: new DatabaseSync(/*turbopackIgnore: true*/ file, { readOnly: true }),
    file,
    mtimeMs,
    checkedAt: Date.now(),
  };
  return current.db;
}

function sqliteDb(db: DatabaseSync): Db {
  // node:sqlite returns null-prototype rows; normalise to plain objects.
  return {
    kind: "sqlite",
    like: "LIKE",
    async all<T>(sql: string, ...params: Param[]) {
      return db.prepare(sql).all(...params).map((r) => ({ ...r }) as T);
    },
    async get<T>(sql: string, ...params: Param[]) {
      const r = db.prepare(sql).get(...params);
      return r ? ({ ...r } as T) : undefined;
    },
  };
}

// ---------------------------------------------------------------- entry

export async function getDb(): Promise<Db> {
  const url = process.env.DATABASE_URL;
  return url ? postgresDb(url) : sqliteDb(await openSnapshot());
}
