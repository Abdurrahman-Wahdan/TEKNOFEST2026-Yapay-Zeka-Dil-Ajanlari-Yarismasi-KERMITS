"use client";

import { File as FileGlyph, Image as ImageGlyph } from "lucide-react";

import { ContextGlyph } from "./ContextGlyph";
import { useTranslations } from "next-intl";

import { VuiBox, VuiTypography } from "@/components/vision";
import type { MentionTarget } from "@/lib/chat/types";

/**
 * The `@` menu: which staged document the user means.
 *
 * A question about "the statement" is ambiguous the moment two files are
 * attached, so `@` is how the user points at one. The menu only ever lists what
 * is actually staged -- there is nothing to mention until something is attached,
 * and offering names that are not there would invite the agent to be asked about
 * a file it was never given.
 *
 * Rendered below the composer rather than at the caret, which is where ChatGPT
 * puts it. Caret-tracking in a textarea means mirroring its content into a hidden
 * element to measure, and the composer's edge is an unambiguous anchor that
 * cannot end up half off-screen on a narrow viewport.
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
        // Below the composer, clear of it.
        top: "calc(100% + 8px)",
        zIndex: 3,
        maxHeight: 220,
        overflowY: "auto",
        borderRadius: "var(--radius-md)",
        backgroundColor: "var(--popover)",
        border: "1px solid var(--border)",
        boxShadow: "0 8px 24px rgb(0 0 0 / 0.18)",
        py: 0.5,
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
              backgroundColor: index === activeIndex ? "var(--muted)" : "transparent",
              "&:hover": { backgroundColor: "var(--muted)" },
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
              color="white"
              sx={{
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

/**
 * The `@token` the caret is currently inside, if any.
 *
 * Returns the token's start offset and the text typed after the `@`, or null
 * when the caret is not in a mention. Requires the `@` to start a word -- an
 * email address in the middle of a sentence must not open the menu.
 */
export function mentionAt(value: string, caret: number): { start: number; query: string } | null {
  // Scan back from the caret to the nearest whitespace; that is the token.
  let start = caret;
  while (start > 0 && !/\s/.test(value[start - 1])) start -= 1;
  if (value[start] !== "@") return null;

  const query = value.slice(start + 1, caret);
  // A space ends the mention, so "@ " is not an open menu.
  if (/\s/.test(query)) return null;
  return { start, query };
}
