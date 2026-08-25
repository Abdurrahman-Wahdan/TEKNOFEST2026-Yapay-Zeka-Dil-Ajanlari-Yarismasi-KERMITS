"use client";

import Popper from "@mui/material/Popper";
import { useTheme } from "@mui/material/styles";
import { Quote } from "lucide-react";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useMemo, useState } from "react";

import { VuiBox, VuiTypography } from "@/components/vision";
import { usePathname } from "@/i18n/navigation";
import { useAuth } from "@/lib/auth";
import { useChat } from "@/lib/chat/ChatProvider";
import {
  elideLabel,
  formatSurroundingRow,
  normaliseQuote,
} from "@/lib/chat/context-format";
import {
  describeLocation,
  elementOf,
  readRowCells,
  type ContextLocation,
  type RowCell,
} from "@/lib/chat/page-locator";

/**
 * Select anything, and ask the assistant about it.
 *
 * The quote becomes a staged *attachment*, not text pushed into the composer.
 * That is the whole reason this component can be this small: the composer's value
 * is local state in a component that exists twice and may not be mounted at all,
 * so reaching into it would mean lifting the most delicately-balanced state in the
 * app. It is also what ChatGPT does, and it sidesteps a real bug -- the user's own
 * words are deliberately never rendered as markdown, so a `> quoted line` would
 * show up as a literal `>`.
 *
 * Mounted once, in `VisionApp`, beside `AgentPopup`: one listener for the whole
 * dashboard rather than one per page.
 */

/** Distance from the selection to the button. */
const OFFSET = 8;

/**
 * Where a selection is *not* offered a reply button.
 *
 * A selection inside the composer is the user editing their own question, and a
 * button over it would cover the text they are working on. `data-no-quote` is the
 * escape hatch for anywhere else that needs to opt out later.
 */
const EXCLUDED = "textarea, input, [contenteditable='true'], [data-no-quote]";

export function SelectionReply() {
  const t = useTranslations("chat");
  const theme = useTheme();
  const pathname = usePathname();
  const { user } = useAuth();
  const { attachments, setPopupOpen } = useChat();

  /**
   * The selection, snapshotted.
   *
   * A plain rect and a plain string, never the live `Range`: a `Range` mutates
   * under you as the selection changes, and MUI's own virtual-element demo is
   * on record crashing because `getRangeAt(0)` was called when `rangeCount` had
   * dropped to zero. Snapshotting also means the button cannot re-measure itself
   * into a different place mid-hover.
   */
  const [selection, setSelection] = useState<{
    rect: DOMRect;
    text: string;
    /**
     * Where it came from, read at selection time.
     *
     * Read now rather than on press: the page can re-render between the two -- a
     * table can re-sort under a live selection -- and the row the user pointed at
     * is the row that should travel.
     */
     location: ContextLocation;
    /**
     * The row the selection sits in, when it is inside a table.
     *
     * Read at selection time with everything else: a table can re-sort under a
     * live selection, and the row the user pointed at is the row that should
     * travel.
     */
    rowCells?: RowCell[];
  } | null>(null);

  const clear = useCallback(() => setSelection(null), []);

  useEffect(() => {
    const read = () => {
      const sel = document.getSelection();
      // `rangeCount` before `getRangeAt`, always.
      if (!sel || sel.isCollapsed || sel.rangeCount === 0) return clear();

      const range = sel.getRangeAt(0);
      /**
       * Where the selection *is*, never where the focus is.
       *
       * An earlier version also rejected the selection when `activeElement` sat
       * in a field, meaning to stop a parked caret being quoted. On /chat the
       * composer is autofocused, so that killed the button on the one surface
       * where quoting the agent's own answer matters most -- and it was never
       * needed: a textarea keeps its own selection, which does not appear in
       * `document.getSelection()` at all, and a `contenteditable` selection is
       * caught by this ancestor check.
       */
      const host = elementOf(range.commonAncestorContainer);
      if (!host || host.closest(EXCLUDED)) return clear();

      const raw = sel.toString();
      const text = normaliseQuote(raw);
      if (!text) return clear();

      const rect = range.getBoundingClientRect();
      // A zero-area rect has nowhere to anchor -- it happens for a selection
      // inside a collapsed or hidden element.
      if (rect.width === 0 && rect.height === 0) return clear();

      setSelection({
        rect,
        text,
        location: describeLocation(range.commonAncestorContainer, pathname),
        rowCells: readRowCells(range.commonAncestorContainer),
      });
    };

    /**
     * Read after the browser has settled the selection, not during the gesture.
     *
     * On `mouseup` the selection is final; `selectionchange` fires on every pixel
     * of a drag, and showing a button from there makes it flicker across the
     * screen while the user is still choosing what to select.
     */
    const onSettle = () => {
      requestAnimationFrame(read);
    };

    /** Collapsing hides it -- a plain click anywhere must take the button away. */
    const onSelectionChange = () => {
      const sel = document.getSelection();
      if (!sel || sel.isCollapsed || sel.rangeCount === 0) clear();
    };

    document.addEventListener("mouseup", onSettle);
    // Shift+arrow selects too, and a keyboard user needs the same button.
    document.addEventListener("keyup", onSettle);
    document.addEventListener("selectionchange", onSelectionChange);
    // The rect is a snapshot in viewport coordinates, so anything that moves the
    // page underneath it makes the button point at the wrong words.
    window.addEventListener("scroll", clear, true);
    window.addEventListener("resize", clear);

    return () => {
      document.removeEventListener("mouseup", onSettle);
      document.removeEventListener("keyup", onSettle);
      document.removeEventListener("selectionchange", onSelectionChange);
      window.removeEventListener("scroll", clear, true);
      window.removeEventListener("resize", clear);
    };
  }, [clear, pathname]);

  const anchor = useMemo(
    () => (selection ? { getBoundingClientRect: () => selection.rect } : null),
    [selection],
  );

  const reply = useCallback(() => {
    if (!selection) return;
    const { text, location, rowCells } = selection;

    /**
     * The quote, and the row it came out of.
     *
     * The quote stays first and the row is labelled as background, so the agent
     * can tell what was asked about from what merely surrounds it. Only for a
     * table cell -- a quote from prose has no row, and the heading above it is
     * already carried as a coordinate.
     */
    const body =
      rowCells && rowCells.length > 1
        ? `${text}\n\n---\n\nThe row this came from:\n\n${formatSurroundingRow(
            rowCells,
            location.column,
          )}`
        : text;

    attachments.addContext({
      kind: "quote",
      label: elideLabel(text),
      body,
      format: "markdown",
      location,
    });

    // Drop the selection, or the button stays over words the user has already
    // dealt with and a second press attaches the same quote twice.
    document.getSelection()?.removeAllRanges();
    clear();

    // On /chat the composer is already on screen; anywhere else the panel is
    // where the quote just went, and opening it is the only thing that shows the
    // press did something.
    if (pathname !== "/chat") setPopupOpen(true);
  }, [selection, attachments, pathname, setPopupOpen, clear]);

  // Signed out there is nobody to ask on behalf of, exactly as `AgentPopup`.
  if (!user) return null;
  if (!anchor) return null;

  return (
    <Popper
      open
      anchorEl={anchor}
      placement="top"
      // Above the popup panel (drawer + 2), so a quote taken from the agent's own
      // answer is not covered by the panel it came from.
      sx={{ zIndex: theme.zIndex.drawer + 3 }}
      modifiers={[
        { name: "offset", options: { offset: [0, OFFSET] } },
        // Flip below the selection rather than off the top of the window when the
        // selected text is on the first line of the page.
        { name: "flip", options: { fallbackPlacements: ["bottom"] } },
        { name: "preventOverflow", options: { padding: 8 } },
      ]}
    >
      <VuiBox
        component="button"
        type="button"
        // `mousedown` would fire before the browser finishes the click and the
        // selection would still be live; but the bigger reason is that the
        // default action of a mousedown on a button collapses the selection,
        // which would clear it before the handler could read it.
        onMouseDown={(event: React.MouseEvent) => event.preventDefault()}
        onClick={reply}
        display="flex"
        alignItems="center"
        gap={0.75}
        px={1.25}
        py={0.75}
        sx={{
          cursor: "pointer",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-full)",
          backgroundColor: "var(--popover)",
          color: "var(--control-ink)",
          boxShadow: "0 4px 16px rgb(0 0 0 / 0.18)",
          transition: "border-color 150ms ease, color 150ms ease",
          "&:hover": { borderColor: "var(--ring)", color: "var(--foreground)" },
          "&:focus-visible": { outline: "2px solid var(--ring)", outlineOffset: 2 },
        }}
      >
        <Quote size={14} aria-hidden="true" />
        <VuiTypography variant="caption" fontWeight="medium" color="white">
          {t("reply")}
        </VuiTypography>
      </VuiBox>
    </Popper>
  );
}
