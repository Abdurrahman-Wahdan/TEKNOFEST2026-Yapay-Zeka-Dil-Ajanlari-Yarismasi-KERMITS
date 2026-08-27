"use client";

import CircularProgress from "@mui/material/CircularProgress";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { IoAlertCircleOutline, IoSparkles } from "react-icons/io5";

import { ActionButton } from "@/components/ui/ActionButton";
import { CollapsibleCard } from "@/components/ui/CollapsibleCard";
import { VuiBox, VuiTypography } from "@/components/vision";
import type { TableOverviewOut } from "@/lib/api";

/**
 * What the model made of the table underneath it, drawn.
 *
 * Presentation only: it is handed a state and renders it. Where that state came
 * from is the caller's problem, and there are two callers with genuinely
 * different sources — `TableOverview` reads a row keyed on a pool table's id,
 * `LiveOverview` posts the page and polls a digest. The card, its three states
 * and its wording are the same thing in both places, so they are here once. A
 * second copy is how one of them ends up with a different disclaimer from the
 * other, which is the sentence that matters most on it.
 *
 * Above the table on purpose, wherever it is used: it answers "is this table
 * worth reading" before the reader has to work that out from twenty columns of
 * figures themselves.
 *
 * **Hidden from the page outline.** The overview is written from that outline,
 * so a card left visible to it would feed the model its own previous answer as
 * part of the page -- a summary of a summary, and one that drifts further from
 * the table with every refresh. It costs nothing on the pool's card, which is
 * written once; it is load-bearing on the live one, which rewrites itself every
 * five minutes on the FX board.
 */
export function OverviewCard({
  working,
  failed,
  overview,
  onRetry,
}: {
  working: boolean;
  failed: boolean;
  overview: TableOverviewOut | null;
  onRetry: () => void;
}) {
  const t = useTranslations("overview");

  return (
    <VuiBox data-no-outline="">
      <CollapsibleCard
        title={
          <VuiBox display="flex" alignItems="center" gap="8px">
            <VuiBox color="white" display="flex">
              <IoSparkles size="17px" />
            </VuiBox>
            {t("title")}
          </VuiBox>
        }
      >
        {working && (
          <VuiBox display="flex" alignItems="center" gap="10px" mt={2}>
            <CircularProgress size={16} color="info" />
            <VuiTypography variant="button" color="text" fontWeight="regular">
              {t("working")}
            </VuiTypography>
          </VuiBox>
        )}

        {!working && failed && (
          <VuiBox display="flex" alignItems="center" gap="12px" flexWrap="wrap" mt={2}>
            <VuiBox color="text" display="flex">
              <IoAlertCircleOutline size="18px" />
            </VuiBox>
            <VuiTypography variant="button" color="text" fontWeight="regular">
              {t("failed")}
            </VuiTypography>
            <ActionButton variant="outlined" color="white" onClick={onRetry}>
              {t("retry")}
            </ActionButton>
          </VuiBox>
        )}

        {!working && overview && <Overview overview={overview} t={t} />}
      </CollapsibleCard>
    </VuiBox>
  );
}

function Overview({
  overview,
  t,
}: {
  overview: TableOverviewOut;
  t: ReturnType<typeof useTranslations<"overview">>;
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
      <Verdict label={t("recommended")} entries={recommended} />
      <Verdict label={t("notRecommended")} entries={notRecommended} />

      {overview.caveat && (
        <VuiBox>
          <Label>{t("caveat")}</Label>
          <Paragraph>{overview.caveat}</Paragraph>
        </VuiBox>
      )}

      {/* Said plainly, every time: this was written by a model from the table
          below it, and the table is the source of record. */}
      <VuiTypography variant="caption" color="text">
        {t("disclaimer")}
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
