"use client";

import CircularProgress from "@mui/material/CircularProgress";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { useEffect, type ReactNode } from "react";
import { IoAlertCircleOutline, IoSparkles } from "react-icons/io5";

import { ActionButton } from "@/components/ui/ActionButton";
import { CollapsibleCard } from "@/components/ui/CollapsibleCard";
import { VuiBox, VuiTypography } from "@/components/vision";
import { api, type TableOverviewOut } from "@/lib/api";
import { readPageText } from "@/lib/chat/tools";

/** How often to ask whether it is written yet. The work takes a minute or
    more, so a tighter poll would only add lines to the server log.

    There is deliberately no give-up timer beside this. The server says whether
    a generation is still running, and it always stops eventually — its own
    retry window and queue wait see to that — so the card waits exactly as long
    as the work takes rather than as long as a number here guessed. A timer was
    tried first and was wrong in both directions: too short for a busy host,
    and still spinning at a dead one. */
const POLL_MS = 5_000;

/**
 * What the model made of the table underneath it.
 *
 * Above the table on purpose: it answers "is this table worth reading" before
 * the reader has to work that out from twenty columns of figures themselves.
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
 */
export function TableOverview({ tableId, ready }: { tableId: string; ready: boolean }) {
  const t = useTranslations("compareTables");
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

  // One automatic attempt, and only once the table has painted -- the
  // screenshot is of whatever is on screen, so capturing before the rows
  // arrive would send the model a loading state and ask it what it means.
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
    <CollapsibleCard
      title={
        <VuiBox display="flex" alignItems="center" gap="8px">
          <VuiBox color="white" display="flex">
            <IoSparkles size="17px" />
          </VuiBox>
          {t("overviewTitle")}
        </VuiBox>
      }
    >
      {working && (
        <VuiBox display="flex" alignItems="center" gap="10px" mt={2}>
          <CircularProgress size={16} color="info" />
          <VuiTypography variant="button" color="text" fontWeight="regular">
            {t("overviewWorking")}
          </VuiTypography>
        </VuiBox>
      )}

      {!working && failed && (
        <VuiBox display="flex" alignItems="center" gap="12px" flexWrap="wrap" mt={2}>
          <VuiBox color="text" display="flex">
            <IoAlertCircleOutline size="18px" />
          </VuiBox>
          <VuiTypography variant="button" color="text" fontWeight="regular">
            {t("overviewFailed")}
          </VuiTypography>
          <ActionButton
            variant="outlined"
            color="white"
            onClick={() => generate.mutate()}
          >
            {t("overviewRetry")}
          </ActionButton>
        </VuiBox>
      )}

      {!working && overview && <Overview overview={overview} t={t} />}
    </CollapsibleCard>
  );
}

function Overview({
  overview,
  t,
}: {
  overview: TableOverviewOut;
  t: ReturnType<typeof useTranslations<"compareTables">>;
}) {
  // The generated types make these optional because the API defaults them to
  // empty lists; an absent list and an empty one mean the same thing here.
  const recommended = overview.recommended ?? [];
  const notRecommended = overview.not_recommended ?? [];

  return (
    <VuiBox display="flex" flexDirection="column" gap="14px" mt={2}>
      <Paragraph>{overview.summary}</Paragraph>

      {/* Prose, and only the verdict. The table is directly below this card
          and the reader can sort it themselves, so anything drawn here in rows
          is the same data twice -- and the sortable copy wins. */}
      <Verdict label={t("overviewRecommended")} entries={recommended} />
      <Verdict label={t("overviewNotRecommended")} entries={notRecommended} />

      {overview.caveat && (
        <VuiBox>
          <Label>{t("overviewCaveat")}</Label>
          <Paragraph>{overview.caveat}</Paragraph>
        </VuiBox>
      )}

      {/* Said plainly, every time: this was written by a model from the table
          below it, and the table is the source of record. */}
      <VuiTypography variant="caption" color="text">
        {t("overviewDisclaimer")}
      </VuiTypography>
    </VuiBox>
  );
}

/** One verdict list -- the picks, or the ones to skip. Absent when the model
    had no honest basis for it, which is a real answer and not a gap. */
function Verdict({
  label,
  entries,
}: {
  label: string;
  entries: { bank: string; why: string }[];
}) {
  if (entries.length === 0) return null;
  return (
    <VuiBox>
      <Label>{label}</Label>
      {entries.map((entry, index) => (
        <Paragraph key={`${entry.bank}-${index}`}>
          <Strong>{entry.bank}</Strong>
          {` — ${entry.why}`}
        </Paragraph>
      ))}
    </VuiBox>
  );
}

/** A line of the overview. Reads as text, wraps as text, has no columns. */
function Paragraph({ children }: { children: ReactNode }) {
  return (
    <VuiTypography
      variant="button"
      color="text"
      fontWeight="regular"
      sx={{ display: "block", lineHeight: 1.7, marginTop: "6px" }}
    >
      {children}
    </VuiTypography>
  );
}

/** The bank being spoken about, inline in its own sentence. */
function Strong({ children }: { children: ReactNode }) {
  return (
    <VuiTypography component="span" variant="button" color="white" fontWeight="medium">
      {children}
    </VuiTypography>
  );
}

function Label({ children }: { children: ReactNode }) {
  return (
    <VuiTypography variant="caption" color="text" sx={{ display: "block", opacity: 0.8 }}>
      {children}
    </VuiTypography>
  );
}
