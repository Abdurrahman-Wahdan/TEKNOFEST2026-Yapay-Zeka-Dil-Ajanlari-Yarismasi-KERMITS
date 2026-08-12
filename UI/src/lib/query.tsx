"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { ApiError } from "./api";

/**
 * Defaults chosen for what this data actually is.
 *
 * Bank quotes are live calls to the banks' own calculators — slow (a fan-out
 * across ten banks is a second or so) and rate-limited by WAFs that treat a
 * burst from one address as an attack. So: no refetch on window focus, and a
 * stale time long enough that switching tabs does not re-ask ten banks.
 */
function makeClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 60_000,
        gcTime: 5 * 60_000,
        refetchOnWindowFocus: false,
        retry: (failureCount, error) => {
          // A refusal is an answer, not a failure. Retrying "this bank does not
          // sell that product" three times just delays showing it to the user.
          if (error instanceof ApiError) {
            if (error.isRefusal || error.isUnauthenticated) return false;
          }
          return failureCount < 2;
        },
      },
    },
  });
}

export function QueryProvider({ children }: { children: ReactNode }) {
  // In state, not a module constant: a module-level client is shared across
  // requests on the server, which leaks one user's cached data into another's
  // render.
  const [client] = useState(makeClient);

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
