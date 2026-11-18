import "server-only";

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";

/**
 * The web app reads ONE file: the snapshot written by
 * `python -m src.export_snapshot`. It is the contract between the Python
 * pipeline and this app - detection results are precomputed there, so
 * nothing here re-derives statistics.
 *
 * Local dev: ../snapshot/dashboard.db (or INCIDENTS_SNAPSHOT_PATH).
 * Hosted:    downloaded from the dashboard-data branch, refreshed at most
 *            once an hour (it changes once a day).
 */

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

function openFile(file: string): Open {
  const db = new DatabaseSync(/*turbopackIgnore: true*/ file, { readOnly: true });
  return { db, file, mtimeMs: fs.statSync(/*turbopackIgnore: true*/ file).mtimeMs, checkedAt: Date.now() };
}

/** Returns an open read-only connection, reopening when the file changed. */
export async function getDb(): Promise<DatabaseSync> {
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
  current = openFile(file);
  return current.db;
}

/** node:sqlite returns null-prototype rows; normalise to plain typed objects. */
export function all<T>(db: DatabaseSync, sql: string, ...params: (string | number | null)[]): T[] {
  return db.prepare(sql).all(...params).map((r) => ({ ...r }) as T);
}

export function get<T>(db: DatabaseSync, sql: string, ...params: (string | number | null)[]): T | undefined {
  const r = db.prepare(sql).get(...params);
  return r ? ({ ...r } as T) : undefined;
}
