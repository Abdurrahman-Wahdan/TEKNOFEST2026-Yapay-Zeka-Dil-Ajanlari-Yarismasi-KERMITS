"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { VuiBox, VuiTypography } from "@/components/vision";
import { api } from "@/lib/api";
import { ringColor } from "@/lib/chat/context-ring";

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

/** The hit area, matching every other control in the row. */
const BUTTON = 36;

/**
 * The drawn ring, sized to the row's *glyphs* rather than to its buttons.
 *
 * Measured: the plus and the eye each render a 20px glyph inside a 36px button,
 * leaving 8px of clear space either side. This SVG used to fill its button edge
 * to edge, so its mark sat 5px from its neighbours where theirs sat 8px -- and
 * the row read as unevenly spaced even though every gap was exactly 6px. Same
 * optical size, same insets, even row.
 */
const GLYPH = 20;

/** One padding value for the hover box, used above, below and to each side. */
const TOOLTIP_PAD_PX = 10;
const BAR_HEIGHT_PX = 4;
const STROKE = 2.5;
const RADIUS = (GLYPH - STROKE) / 2;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

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
  const [hovered, setHovered] = useState(false);

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
  // Blue while it fills, amber as it nears compaction, red when compaction is a
  // turn or two away. Before the first turn there is no thread to describe, so
  // the empty track is all there is to draw and the stroke colour is moot.
  const color = level
    ? ringColor(level.used_tokens, level.compact_at_tokens)
    : "var(--primary)";

  return (
    <VuiBox
      component="button"
      type="button"
      onClick={(event: React.MouseEvent) => {
        event.stopPropagation();
        onToggle();
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
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
        width: BUTTON,
        height: BUTTON,
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
      <svg width={GLYPH} height={GLYPH} viewBox={`0 0 ${GLYPH} ${GLYPH}`} aria-hidden>
        <circle
          cx={GLYPH / 2}
          cy={GLYPH / 2}
          r={RADIUS}
          fill="none"
          stroke="var(--border-strong)"
          strokeWidth={STROKE}
        />
        <circle
          cx={GLYPH / 2}
          cy={GLYPH / 2}
          r={RADIUS}
          fill="none"
          stroke={color}
          strokeWidth={STROKE}
          strokeLinecap="round"
          strokeDasharray={CIRCUMFERENCE}
          strokeDashoffset={CIRCUMFERENCE * (1 - fraction)}
          // From the top, clockwise. The default start is 3 o'clock, which
          // reads as an arbitrary arc rather than as a gauge filling up.
          transform={`rotate(-90 ${GLYPH / 2} ${GLYPH / 2})`}
          style={{ transition: "stroke-dashoffset 300ms ease, stroke 300ms ease" }}
        />
      </svg>
      {/* The figures on hover, so the ring can be read without being clicked.
          Hidden while the menu is open: the same numbers are already on screen,
          in more detail, eight pixels above this. */}
      {hovered && !open && level && (
        <VuiBox
          role="tooltip"
          sx={{
            position: "absolute",
            right: 0,
            bottom: "calc(100% + 8px)",
            zIndex: 4,
            // Never wider than the composer it sits in -- in a 420px popup a
            // fixed width would hang off the panel.
            maxWidth: "100%",
            width: "max-content",
            // One padding value, so the bar below sits the same distance from
            // every edge as the line above it.
            p: `${TOOLTIP_PAD_PX}px`,
            pointerEvents: "none",
            borderRadius: "var(--radius-md)",
            backgroundColor: "var(--popover)",
            border: "1px solid var(--border)",
            boxShadow: "0 8px 24px rgb(0 0 0 / 0.18)",
          }}
        >
          <VuiTypography
            variant="caption"
            sx={{
              display: "block",
              color: "var(--foreground)",
              whiteSpace: "nowrap",
              // The type's own leading would otherwise put more space under the
              // line than the padding puts above it.
              lineHeight: 1.4,
            }}
          >
            {t("contextTooltip", {
              percent: Math.round(level.fraction * 100),
              tokens: level.tokens_until_compaction.toLocaleString(),
            })}
          </VuiTypography>

          {/* The same reading as the ring, laid flat: a bar shows how much room
              is left, which a circle can only imply. */}
          <VuiBox
            sx={{
              mt: `${TOOLTIP_PAD_PX}px`,
              height: BAR_HEIGHT_PX,
              borderRadius: "var(--radius-full)",
              backgroundColor: "var(--border-strong)",
              position: "relative",
              overflow: "hidden",
            }}
          >
            <VuiBox
              sx={{
                width: `${level.fraction * 100}%`,
                height: "100%",
                borderRadius: "var(--radius-full)",
                backgroundColor: color,
                transition: "width 300ms ease, background-color 300ms ease",
              }}
            />
            {/* Where compaction happens on its own. Without it the colour
                changes have no visible cause -- the bar turns amber at a point
                the user cannot see. */}
            {level.usable_tokens > 0 && (
              <VuiBox
                sx={{
                  position: "absolute",
                  top: 0,
                  bottom: 0,
                  left: `${Math.min(
                    (level.compact_at_tokens / level.usable_tokens) * 100,
                    100,
                  )}%`,
                  width: "2px",
                  backgroundColor: "var(--popover)",
                }}
              />
            )}
          </VuiBox>
        </VuiBox>
      )}
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
        // Never wider than the composer: a fixed 288px hangs off a 420px popup.
        maxWidth: "100%",
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
