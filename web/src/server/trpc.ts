import "server-only";

import { initTRPC } from "@trpc/server";
import type { DatabaseSync } from "node:sqlite";
import { getDb } from "./db";

export async function createContext(): Promise<{ db: DatabaseSync }> {
  return { db: await getDb() };
}

type Context = Awaited<ReturnType<typeof createContext>>;

const t = initTRPC.context<Context>().create();

export const router = t.router;
// Read-only dashboard: every procedure is public.
export const publicProcedure = t.procedure;
