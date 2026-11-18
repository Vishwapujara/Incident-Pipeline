import "server-only";

import { initTRPC } from "@trpc/server";
import { getDb, type Db } from "./db";

export async function createContext(): Promise<{ db: Db }> {
  return { db: await getDb() };
}

type Context = Awaited<ReturnType<typeof createContext>>;

const t = initTRPC.context<Context>().create();

export const router = t.router;
// Read-only dashboard: every procedure is public.
export const publicProcedure = t.procedure;

/**
 * Restricts raw_incidents (alias r) to the pipeline's data window. The
 * SQLite snapshot is already trimmed to it; Postgres holds full history.
 */
export const IN_WINDOW = "r.created_at >= (SELECT value FROM dashboard_meta WHERE key = 'data_cutoff_date')";
