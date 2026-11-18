import { fetchRequestHandler } from "@trpc/server/adapters/fetch";
import { appRouter } from "@/server/root";
import { createContext } from "@/server/trpc";

// node:sqlite needs the Node runtime, not Edge.
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const handler = (req: Request) =>
  fetchRequestHandler({
    endpoint: "/api/trpc",
    req,
    router: appRouter,
    createContext,
    onError: ({ path, error }) => console.error(`tRPC ${path ?? "?"}:`, error),
  });

export { handler as GET, handler as POST };
