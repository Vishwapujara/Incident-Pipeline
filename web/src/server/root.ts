import "server-only";

import type { inferRouterOutputs } from "@trpc/server";
import { coverageRouter } from "./routers/coverage";
import { incidentsRouter } from "./routers/incidents";
import { overviewRouter } from "./routers/overview";
import { patternsRouter } from "./routers/patterns";
import { trendsRouter } from "./routers/trends";
import { router } from "./trpc";

export const appRouter = router({
  overview: overviewRouter,
  patterns: patternsRouter,
  trends: trendsRouter,
  coverage: coverageRouter,
  incidents: incidentsRouter,
});

export type AppRouter = typeof appRouter;
export type RouterOutputs = inferRouterOutputs<AppRouter>;
