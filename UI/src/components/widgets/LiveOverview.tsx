"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useLocale } from "next-intl";
import { useEffect, useRef } from "react";

import { api } from "@/lib/api";
import { readPageText } from "@/lib/chat/tools";

import { OverviewCard } from "./OverviewCard";
import { POLL_MS } from "./TableOverview";

/**
 * The overview of whatever `/compare` is showing right now.
 *
 * The sibling of `TableOverview`, and the same card — only the source differs.
 * There is no id to key on here: the comparator builds its board from bank
 * endpoints and the user's own inputs, so the page *is* the key. The client
 * posts the outline, the server hashes it and hands the digest back, and the
 * poll runs on that.
 *
 * **`revision` is the whole of the trigger.** A live page has no natural moment
 * to summarise — the FX board moves every three seconds and a comparison
 * appears when the user presses a button — so the caller says when, in the only
 * terms it can be said in: a string that changes when the thing on screen has
 * become a different thing. What that string is built from is per category and
 * lives in `Comparator`, because only it knows the difference between a board
 * that ticked and a comparison that was re-run.
 *
 * Regenerating is not as expensive as it looks. The server keys on the outline,
 * so a revision that fires over a page which has not actually changed serves the
 * cache and costs nothing — which is what makes a five-minute FX refresh
 * affordable on a board that closed for the weekend.
 */
export function LiveOverview({
  ready,
  revision,
}: {
  /** The table is on screen and painted. Reading before it is asks the model
      what a loading state means. */
  ready: boolean;
  /** Changes when what is on screen has become a different thing. */
  revision: string;
}) {
  const locale = useLocale();

  /**
   * Ask for one, tagged with the revision it answers.
   *
   * The revision travels as the mutation's variable and comes back on its own
   * result, rather than being kept in a second piece of state beside it. That
   * is what makes the stale case impossible: `useMutation` holds the previous
   * `data` until the next call resolves, so reading it untagged would show the
   * old comparison's verdict for the second or two between pressing Compare and
   * the POST coming back — a stale verdict under a table that has already
   * changed, which is the one way this card can be actively wrong.
   *
   */
  const generate = useMutation({
    mutationFn: async (forRevision: string) => {
      const text = readPageText();
      if (!text) throw new Error("There is no page on screen to read.");
      const state = await api.startLiveOverview({ locale, page: { text } });
      return { revision: forRevision, state };
    },
  });

  /**
   * The revision already asked for. A ref, and that is the whole point.
   *
   * `generate.variables` looks like the same guard and is not: it is render
   * state, so two renders queued in one tick both read the value from *before*
   * either of them fired, and both fire. Measured, not theorised -- pressing
   * Compare a second time sent two identical POSTs 0ms apart. A ref is written
   * synchronously, so the second run of the effect sees the first one's write
   * and stops.
   *
   * A failed attempt leaves it set rather than looping on the error; the retry
   * button is the way back.
   */
  const asked = useRef<string | null>(null);

  useEffect(() => {
    if (!ready || asked.current === revision) return;
    asked.current = revision;
    generate.mutate(revision);
    // `generate` is a stable object; listing it would re-run this on every
    // status change it makes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, revision]);

  // The answer to *this* revision, or nothing. The POST may already carry the
  // overview: an unchanged page comes back `ready` from the server's cache,
  // with no generation and nothing to wait for.
  const started = generate.data?.revision === revision ? generate.data.state : null;
  const digest = started?.digest ?? null;

  const polled = useQuery({
    queryKey: ["live-overview", digest, locale],
    queryFn: () => api.liveOverview(digest as string, locale),
    // Only while one is being written. Once it is ready or gone this stops on
    // its own — an overview nobody asked for will not appear by itself.
    enabled: Boolean(digest) && started?.status === "generating",
    refetchInterval: (query) =>
      query.state.data?.status === "generating" ? POLL_MS : false,
  });

  // The POST's own answer when it served the cache, otherwise the poll's.
  const state = started?.status === "ready" ? started : polled.data;
  const overview = state?.status === "ready" ? state.overview ?? null : null;
  // A generation the server accepted, is no longer running, and which left
  // nothing behind, failed. That is the server's answer rather than a timeout
  // this component guessed at. `generate.isError` is read against the current
  // revision so a failure on the previous comparison does not draw an error
  // over the new one while it is still being written.
  const abandoned = polled.data?.status === "missing";
  const failed =
    (generate.isError && generate.variables === revision) || polled.isError || abandoned;
  // Deliberately spinning rather than showing the previous overview while a new
  // one is written. The old one is a verdict on the *old* results, and leaving
  // it under a table that has changed underneath it is the one way this card
  // can be actively wrong.
  const working = !overview && !failed;

  return (
    <OverviewCard
      working={working}
      failed={failed}
      overview={overview}
      onRetry={() => {
        asked.current = revision;
        generate.mutate(revision);
      }}
    />
  );
}
