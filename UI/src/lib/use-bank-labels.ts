"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { api } from "@/lib/api";

/**
 * Bank key -> display name.
 *
 * One `queryKey` for every consumer, so several tables on a page — and a
 * comparator that also needs the bank *records* — cost one request between
 * them. A failure here is not a table's problem: the map comes back empty, keys
 * render raw, and everything else on the page still works.
 */
export function useBankLabels(): Record<string, string> {
  const { data: banks } = useQuery({ queryKey: ["banks"], queryFn: api.banks });

  return useMemo(() => {
    const map: Record<string, string> = {};
    for (const bank of banks ?? []) map[bank.name] = bank.display_name;
    return map;
  }, [banks]);
}
