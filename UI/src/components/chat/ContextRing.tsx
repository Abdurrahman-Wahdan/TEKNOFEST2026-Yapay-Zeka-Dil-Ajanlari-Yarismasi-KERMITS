"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";

import { VuiBox, VuiTypography } from "@/components/vision";
import { api, type ContextLevel } from "@/lib/api";

/**
 * How full the conversation is, in the composer, where the mic used to be.
 *
 * The mic was disabled and had been since it was added -- there is no
 * speech-to-text pipeline -- so the row was carrying a control that did nothing
 * while the thing a user actually needs to know about a long conversation had
 * nowhere to live.
 *
 * What it shows is the *supervisor's* thread. Each of the ten bank specialists
 * has its own, compacted on the same terms, but those are private working memory
 * rather than the conversation, and a user cannot act on them.
 *
 * The numbers come from the agent's own middleware (`GET .../context`), not from
 * a second count made for display. Counting separately would drift from the
 * threshold that actually fires, and a meter that disagrees with the behaviour
 * it describes is worse than no meter.
 */

const SIZE = 36;
const STROKE = 2.5;
const RADIUS = (SIZE - STROKE) / 2 - 5;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

/**
 * Colour by nearness to compaction, not by raw fullness.
 *
 * A thread at 60% of its window is at 86% of a 0.7 threshold -- the second
 * number is the one worth reacting to, because compaction is what actually
 * happens next. At rest it is `--control-ink`, the same grey as every other
 * glyph in the row, so a quiet conversation does not decorate the composer.
 */
function ringColor(level: ContextLevel): string {
  if (level.compact_at_tokens <= 0) return "var(--control-ink)";
  const ratio = level.used_tokens / level.compact_at_tokens;
  if (ratio >= 1) return "var(--danger)";
  if (ratio >= 0.75) return "var(--warn)";
  return "var(--control-ink)";
}

export function ContextRing({
  sessionId,
  open,
  onToggle,
}: {
  /** The persisted conversation. Undefined before the first turn creates one. */
  sessionId?: string;
  open: boolean;
  onToggle: () => void;
}) {
  const t = useTranslations("chat");

  const { data: level } = useQuery({
    queryKey: ["contextLevel", sessionId],
    queryFn: () => api.contextLevel(sessionId as string),
    // Nothing to ask about until a turn has created the thread.
    enabled: Boolean(sessionId),
    // Refetched by the composer when a turn finishes rather than on a timer:
    // the level only moves when the conversation does.
    staleTime: Infinity,
  });

  const fraction = level?.fraction ?? 0;
  const color = level ? ringColor(level) : "var(--control-ink)";

  return (
    <VuiBox
      component="button"
      type="button"
      onClick={(event: React.MouseEvent) => {
        event.stopPropagation();
        onToggle();
      }}
      // Disabled rather than hidden before the first turn: a control that
      // appears once you start typing moves the row under the user's cursor.
      disabled={!sessionId}
      aria-haspopup="dialog"
      aria-expanded={open}
      aria-label={t("contextLabel")}
      title={t("contextLabel")}
      display="flex"
      alignItems="center"
      justifyContent="center"
      sx={{
        width: SIZE,
        height: SIZE,
        flexShrink: 0,
        alignSelf: "center",
        border: "none",
        padding: 0,
        borderRadius: "var(--radius-full)",
        backgroundColor: open ? "var(--muted)" : "transparent",
        cursor: sessionId ? "pointer" : "not-allowed",
        opacity: sessionId ? 1 : 0.5,
        transition: "background-color 150ms ease",
        "&:hover:not(:disabled)": { backgroundColor: "var(--muted)" },
        "&:focus-visible": { outline: "2px solid var(--ring)", outlineOffset: 2 },
      }}
    >
      <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`} aria-hidden>
        <circle
          cx={SIZE / 2}
          cy={SIZE / 2}
          r={RADIUS}
          fill="none"
          stroke="var(--border-strong)"
          strokeWidth={STROKE}
        />
        <circle
          cx={SIZE / 2}
          cy={SIZE / 2}
          r={RADIUS}
          fill="none"
          stroke={color}
          strokeWidth={STROKE}
          strokeLinecap="round"
          strokeDasharray={CIRCUMFERENCE}
          strokeDashoffset={CIRCUMFERENCE * (1 - fraction)}
          // From the top, clockwise. The default start is 3 o'clock, which
          // reads as an arbitrary arc rather than as a gauge filling up.
          transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
          style={{ transition: "stroke-dashoffset 300ms ease, stroke 300ms ease" }}
        />
      </svg>
    </VuiBox>
  );
}

export function ContextMenu({
  sessionId,
  onCompacted,
}: {
  sessionId: string;
  onCompacted: () => void;
}) {
  const t = useTranslations("chat");
  const queryClient = useQueryClient();
  const { data: level, isLoading } = useQuery({
    queryKey: ["contextLevel", sessionId],
    queryFn: () => api.contextLevel(sessionId),
    staleTime: Infinity,
  });

  const compact = useMutation({
    mutationFn: () => api.compactSession(sessionId),
    onSuccess: (result) => {
      queryClient.setQueryData(["contextLevel", sessionId], result.context);
      onCompacted();
    },
  });

  const percent = level ? Math.round(level.fraction * 100) : 0;

  return (
    <VuiBox
      role="dialog"
      aria-label={t("contextLabel")}
      sx={{
        position: "absolute",
        right: 0,
        bottom: "calc(100% + 8px)",
        zIndex: 3,
        width: 288,
        // Same shell as the mention list and the Advanced menu.
        borderRadius: "var(--radius-md)",
        backgroundColor: "var(--popover)",
        border: "1px solid var(--border)",
        boxShadow: "0 8px 24px rgb(0 0 0 / 0.18)",
        display: "flex",
        flexDirection: "column",
        gap: 0.5,
        p: 1.5,
      }}
    >
      {isLoading || !level ? (
        <VuiTypography variant="caption" sx={{ color: "var(--control-ink)" }}>
          {t("contextLoading")}
        </VuiTypography>
      ) : (
        <>
          <VuiTypography
            variant="button"
            sx={{ color: "var(--foreground)", fontWeight: "var(--weight-medium)" }}
          >
            {t("contextUsed", { percent })}
          </VuiTypography>

          <VuiTypography variant="caption" sx={{ color: "var(--control-ink)" }}>
            {t("contextTokens", {
              used: level.used_tokens.toLocaleString(),
              total: level.usable_tokens.toLocaleString(),
            })}
          </VuiTypography>

          <VuiTypography variant="caption" sx={{ color: "var(--control-ink)" }}>
            {level.tokens_until_compaction > 0
              ? t("contextUntil", {
                  tokens: level.tokens_until_compaction.toLocaleString(),
                })
              : t("contextDue")}
          </VuiTypography>

          <VuiTypography variant="caption" sx={{ color: "var(--control-ink)" }}>
            {t("contextKeeps", { count: level.keep_messages })}
          </VuiTypography>

          <VuiBox
            component="button"
            type="button"
            onClick={(event: React.MouseEvent) => {
              event.stopPropagation();
              compact.mutate();
            }}
            disabled={compact.isPending}
            display="flex"
            alignItems="center"
            justifyContent="center"
            sx={{
              mt: 1,
              height: 34,
              width: "100%",
              border: "none",
              borderRadius: "var(--radius-full)",
              cursor: compact.isPending ? "wait" : "pointer",
              fontFamily: "inherit",
              fontSize: "0.875rem",
              fontWeight: "var(--weight-medium)",
              backgroundColor: "var(--primary)",
              color: "var(--primary-foreground)",
              "&:hover:not(:disabled)": { backgroundColor: "var(--primary-hover)" },
              "&:disabled": { backgroundColor: "var(--muted)", color: "var(--control-ink)" },
              "&:focus-visible": { outline: "2px solid var(--ring)", outlineOffset: 2 },
            }}
          >
            {compact.isPending ? t("contextCompacting") : t("contextCompact")}
          </VuiBox>

          {compact.isError && (
            <VuiTypography variant="caption" sx={{ color: "var(--danger)", mt: 0.5 }}>
              {t("contextCompactFailed")}
            </VuiTypography>
          )}

          {/* Said plainly, because it is the question a user actually has before
              pressing a button that removes things. */}
          <VuiTypography variant="caption" sx={{ color: "var(--control-ink)", mt: 0.5 }}>
            {t("contextTranscriptSafe")}
          </VuiTypography>
        </>
      )}
    </VuiBox>
  );
}
