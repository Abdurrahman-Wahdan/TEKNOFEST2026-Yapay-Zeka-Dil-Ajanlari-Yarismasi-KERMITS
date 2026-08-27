"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useLocale } from "next-intl";
import { useEffect } from "react";

import { api } from "@/lib/api";
import { readPageText } from "@/lib/chat/tools";

import { OverviewCard } from "./OverviewCard";

/** How often to ask whether it is written yet. The work takes a minute or
    more, so a tighter poll would only add lines to the server log.

    There is deliberately no give-up timer beside this. The server says whether
    a generation is still running, and it always stops eventually — its own
    retry window and queue wait see to that — so the card waits exactly as long
    as the work takes rather than as long as a number here guessed. A timer was
    tried first and was wrong in both directions: too short for a busy host,
    and still spinning at a dead one. */
export const POLL_MS = 5_000;

/**
 * The overview of one table from the offline pool, keyed on that table's id.
 *
 * Written by a **stateless** agent — no conversation, no retrieval, one call —
 * so the same table produces the same overview, which is what makes it
 * cacheable. The server keeps it keyed on a hash of the exact table, so the
 * second visitor pays nothing and a regenerated pool invalidates itself.
 *
 * The page it reads is `readPageText()` — the same outline the assistant's own
 * `look_at_page` tool sends, so the format and the figures are decided in one
 * place. No screenshot: it cost minutes of vision prefill per table and carried
 * nothing the outline does not, once the outline learned to keep short-line
 * cards like "banks that do not offer this".
 *
 * `LiveOverview` is the sibling for `/compare`, where there is no id to key on.
 * Both draw `OverviewCard`; only the source differs.
 */
export function TableOverview({ tableId, ready }: { tableId: string; ready: boolean }) {
  const locale = useLocale();

  const cached = useQuery({
    queryKey: ["table-overview", tableId, locale],
    queryFn: () => api.tableOverview(tableId, locale),
    // Poll only while the server says someone is writing one. An overview
    // nobody asked for is not going to appear on its own.
    refetchInterval: (query) =>
      query.state.data?.status === "generating" ? POLL_MS : false,
  });

  const generate = useMutation({
    mutationFn: async () => {
      const text = readPageText();
      if (!text) throw new Error("There is no page on screen to read.");
      return api.startTableOverview(tableId, { locale, page: { text } });
    },
    // Ask again straight away: the answer is now "generating", which is what
    // starts the polling.
    onSuccess: () => cached.refetch(),
  });

  // One automatic attempt, and only once the table has painted -- the outline
  // is of whatever is on screen, so reading before the rows arrive would send
  // the model a loading state and ask it what it means.
  //
  // "Have we already asked?" is the mutation's own `isIdle`, not a flag beside
  // it: `mutate()` leaves idle synchronously, so the guard closes on the same
  // tick, and there is no second piece of state to keep in step. The mutation
  // resets with the component, which the parent keys by table.
  useEffect(() => {
    if (!ready || !generate.isIdle || cached.data?.status !== "missing") return;
    generate.mutate();
    // `generate` is a stable object; listing it would re-run this on every
    // status change it makes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, generate.isIdle, cached.data?.status]);

  const state = cached.data;
  const overview = state?.status === "ready" ? state.overview ?? null : null;
  // A generation the server accepted, is no longer running, and which left no
  // row behind, failed. That is a fact from the server rather than a timeout
  // this component guessed at.
  const abandoned = generate.isSuccess && state?.status === "missing";
  const failed = cached.isError || generate.isError || abandoned;
  const working =
    !overview &&
    !failed &&
    (cached.isLoading || generate.isPending || state?.status === "generating");

  return (
    <OverviewCard
      working={working}
      failed={failed}
      overview={overview}
      onRetry={() => generate.mutate()}
    />
  );
}
