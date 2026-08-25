"use client";

import { X } from "lucide-react";
import type { ReactNode } from "react";

import { VuiBox } from "@/components/vision";

import { RoundButton } from "./RoundButton";

/**
 * The app's toast: something happened elsewhere, and here is the way to it.
 *
 * Not MUI's `Snackbar`. That arrives with its own surface, its own type scale
 * and its own idea of a "severity" colour, and would be the one floating panel
 * in the app not drawn from our tokens — the same objection that had the
 * composer draw its own switch. This is a card, the way every other card here
 * is a card: `--card` on `--border`, the same radius, the same ink.
 *
 * **The whole toast is the action.** A notification whose point is "go and read
 * this" should not make the reader find a link inside it, so the body is a
 * button and the only other control is dismiss. Dismiss is a real button rather
 * than a click-anywhere-else, because the toast overlaps the page and stealing
 * a click from what is underneath is worse than one more control.
 */
export function Toast({
  title,
  body,
  icon,
  onOpen,
  onDismiss,
  openLabel,
  dismissLabel,
}: {
  title: string;
  body?: ReactNode;
  icon?: ReactNode;
  /** Omit for a toast that only reports; with it, the card becomes a button. */
  onOpen?: () => void;
  onDismiss: () => void;
  openLabel: string;
  dismissLabel: string;
}) {
  return (
    <VuiBox
      display="flex"
      alignItems="flex-start"
      gap="10px"
      sx={{
        width: "min(360px, calc(100vw - 32px))",
        background: "var(--card)",
        border: "1px solid var(--border)",
        borderRadius: "16px",
        padding: "12px 12px 12px 14px",
        // Lifted off the page it covers. The only shadow in the app that is not
        // a focus ring, because this is the only thing that floats over content
        // the user was already reading.
        boxShadow: "0 12px 32px rgba(0, 0, 0, 0.36)",
        pointerEvents: "auto",
      }}
    >
      {icon && (
        <VuiBox
          display="flex"
          alignItems="center"
          justifyContent="center"
          sx={{
            flexShrink: 0,
            width: 32,
            height: 32,
            borderRadius: "var(--radius-full)",
            backgroundColor:
              "color-mix(in srgb, var(--primary) 22%, var(--card))",
            color: "var(--primary-strong)",
            marginTop: "2px",
          }}
        >
          {icon}
        </VuiBox>
      )}

      <VuiBox
        component={onOpen ? "button" : "div"}
        type={onOpen ? "button" : undefined}
        onClick={onOpen}
        aria-label={onOpen ? openLabel : undefined}
        display="flex"
        flexDirection="column"
        gap="2px"
        sx={{
          flex: 1,
          minWidth: 0,
          border: "none",
          padding: 0,
          background: "transparent",
          textAlign: "start",
          fontFamily: "inherit",
          cursor: onOpen ? "pointer" : "default",
          "&:focus-visible": {
            outline: "2px solid var(--ring)",
            outlineOffset: 2,
            borderRadius: "8px",
          },
        }}
      >
        {/* Plain spans rather than `VuiBox component="span"`: VuiBox defaults to
            `color="dark"` and paints it, which inside this button would render
            both lines near-black on the card. */}
        <span
          style={{
            display: "block",
            fontSize: "0.875rem",
            fontWeight: "var(--weight-medium)",
            color: "var(--foreground)",
          }}
        >
          {title}
        </span>
        {body && (
          <span
            style={{
              display: "block",
              fontSize: "0.8125rem",
              color: "var(--control-ink)",
              // One line. A report title can be a whole sentence, and a toast
              // that grows to four lines covers the thing it is announcing.
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {body}
          </span>
        )}
      </VuiBox>

      <RoundButton label={dismissLabel} onClick={onDismiss}>
        <X size={16} />
      </RoundButton>
    </VuiBox>
  );
}

/**
 * Where toasts stack.
 *
 * Top-right, under the navbar, which is the one corner that is right for both
 * reasons that matter. **Semantically**, the bell is up there: a notification
 * that appears beside the control it is about, and leaves the count behind in
 * it, explains itself without a word. **Structurally**, it is the only corner
 * whose offset is a constant — bottom-right is the assistant's floating button
 * on every page, and the left edge is the drawer, which is a 96px rail or an
 * expanded panel depending on a cookie. Anchoring to the left meant picking one
 * of those widths and hiding the toast behind the other.
 *
 * `pointerEvents: none` on the stack with `auto` on each card, so the gaps
 * between toasts do not swallow clicks meant for the page underneath.
 */
export function ToastStack({ children }: { children: ReactNode }) {
  return (
    <VuiBox
      display="flex"
      flexDirection="column"
      gap="10px"
      sx={{
        position: "fixed",
        insetInlineEnd: "24px",
        // Clear of the navbar rather than measured against it: the navbar is
        // the template's and its height changes with its own breakpoints, and a
        // toast that sat 4px too high would overlap the bell it points at.
        top: { xs: "76px", md: "96px" },
        zIndex: 1400,
        pointerEvents: "none",
      }}
    >
      {children}
    </VuiBox>
  );
}
