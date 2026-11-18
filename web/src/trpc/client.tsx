"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createTRPCClient, httpBatchLink } from "@trpc/client";
import { createTRPCContext } from "@trpc/tanstack-react-query";
import { useState, type ReactNode } from "react";
import type { AppRouter } from "@/server/root";

export const { TRPCProvider, useTRPC } = createTRPCContext<AppRouter>();

export function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          // The snapshot changes once a day; a minute is plenty fresh.
          queries: { staleTime: 60_000, refetchOnWindowFocus: false },
        },
      }),
  );
  const [trpcClient] = useState(() =>
    createTRPCClient<AppRouter>({ links: [httpBatchLink({ url: "/api/trpc" })] }),
  );
  return (
    <QueryClientProvider client={queryClient}>
      <TRPCProvider trpcClient={trpcClient} queryClient={queryClient}>
        {children}
      </TRPCProvider>
    </QueryClientProvider>
  );
}
