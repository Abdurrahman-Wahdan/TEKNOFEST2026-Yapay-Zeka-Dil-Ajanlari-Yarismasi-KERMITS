"use client";

import { File as FileGlyph, Image as ImageGlyph } from "lucide-react";

import { ContextGlyph } from "./ContextGlyph";
import { useTranslations } from "next-intl";

import { VuiBox, VuiTypography } from "@/components/vision";
import type { MentionTarget } from "@/lib/chat/types";

/**
 * The `@` menu: which prepared document the user means.
 *
 * A question about "the statement" is ambiguous the moment two files are
 * attached, so `@` is how the user points at one. It lists both the current tray
 * and prepared files sent earlier in this conversation. Historical rows retain
 * their opaque server handle, so selecting one really reattaches the content.
 *
 * Rendered above the composer rather than at the caret. The composer sits at the
 * bottom of an active chat, so opening downward puts the rows below the viewport.
 * The composer's edge is a stable anchor and avoids fragile textarea mirroring.
 */
export function MentionMenu({
  targets,
  activeIndex,
  onPick,
}: {
  targets: MentionTarget[];
  /** Which row the keyboard is on. */
  activeIndex: number;
  onPick: (target: MentionTarget) => void;
}) {
  const t = useTranslations("chat");

  return (
    <VuiBox
      role="listbox"
      aria-label={t("mentionLabel")}
      sx={{
        position: "absolute",
        left: 0,
        right: 0,
        // Above the composer, clear of it and inside the visible chat area.
        bottom: "calc(100% + 8px)",
        zIndex: 20,
        maxHeight: 220,
        overflowY: "auto",
        borderRadius: "var(--radius-md)",
        backgroundColor: "var(--popover)",
        border: "1px solid var(--border)",
        boxShadow: "0 8px 24px rgb(0 0 0 / 0.18)",
        // The drawer's selected page is an inset rounded rectangle rather than
        // an edge-to-edge strip. Mentions use the same gutter and row rhythm.
        display: "flex",
        flexDirection: "column",
        gap: 0.5,
        p: 1,
      }}
    >
      {targets.length === 0 ? (
        <VuiBox px={2} py={1.25}>
          <VuiTypography variant="caption" color="text">
            {t("mentionEmpty")}
          </VuiTypography>
        </VuiBox>
      ) : (
        targets.map((target, index) => (
          <VuiBox
            key={target.id}
            component="button"
            type="button"
            role="option"
            aria-selected={index === activeIndex}
            // `mousedown`, not `click`: the textarea loses focus on mousedown,
            // and by the time a click lands the menu has already closed itself.
            onMouseDown={(event: React.MouseEvent) => {
              event.preventDefault();
              onPick(target);
            }}
            display="flex"
            alignItems="center"
            gap={1.25}
            px={2}
            py={1}
            sx={{
              width: "100%",
              border: "none",
              cursor: "pointer",
              textAlign: "left",
              fontFamily: "inherit",
              // Exact radius used by the drawer's selected navigation row.
              borderRadius: "15px",
              backgroundColor: index === activeIndex ? "var(--muted)" : "transparent",
              transition: "background-color 150ms ease",
              "&:hover": { backgroundColor: "var(--muted)" },
              "&:focus-visible": {
                outline: "2px solid var(--ring)",
                outlineOffset: 2,
              },
            }}
          >
            {/* --control-ink: this glyph is the row's only image/file
                distinction, and --muted-foreground is 3.88:1 on the dark
                surface -- readable enough to notice, not enough to tell an
                image icon from a document one. */}
            <VuiBox display="flex" sx={{ color: "var(--control-ink)", flexShrink: 0 }}>
              {/* Three kinds, so no longer a ternary: a staged table fell
                  through to the document glyph and read as an uploaded file. */}
              {target.kind === "image" ? (
                <ImageGlyph size={16} />
              ) : target.kind === "file" ? (
                <FileGlyph size={16} />
              ) : (
                <ContextGlyph kind={target.contextKind ?? "table"} size={16} />
              )}
            </VuiBox>
            <VuiTypography
              variant="button"
              fontWeight="regular"
              color="inherit"
              sx={{
                color: "var(--foreground)",
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
              }}
            >
              {target.filename}
            </VuiTypography>
          </VuiBox>
        ))
      )}
    </VuiBox>
  );
}
