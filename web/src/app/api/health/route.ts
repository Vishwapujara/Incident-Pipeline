import { getDb } from "@/server/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** Load-balancer health check: the server is up AND the database answers. */
export async function GET() {
  try {
    const db = await getDb();
    await db.get("SELECT 1 AS ok");
    return Response.json({ ok: true, db: db.kind });
  } catch (err) {
    return Response.json({ ok: false, error: (err as Error).message }, { status: 503 });
  }
}
