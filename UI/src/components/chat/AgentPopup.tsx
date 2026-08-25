"use client";

import { useMediaQuery } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import { Maximize2, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { usePathname } from "@/i18n/navigation";
import { useEffect, useRef } from "react";

import { BrandWordmark, BRAND_AI } from "@/components/ui/BrandWordmark";
import { VuiBox } from "@/components/vision";
import { useRouter } from "@/i18n/navigation";
import { useAuth } from "@/lib/auth";
import { useChat } from "@/lib/chat/ChatProvider";

import { ChatHistoryMenu } from "./ChatHistoryMenu";
import { ChatPanel } from "./ChatPanel";

/**
 * The assistant as a floating panel, on every dashboard page.
 *
 * It sits where the light/dark FAB used to. That toggle is not lost: the
 * dashboard navbar has carried a second one all along
 * (`ThemeToggleIconButton`), and `ThemeToggleFab` itself is still exported from
 * `components/VuiThemeToggle` should this ever need to move.
 *
 * The conversation is not held here. It lives in `ChatProvider` in the (app)
 * layout, which is what lets the expand button below be plain navigation: the
 * full page reads the same state, so the transcript is simply already there.
 */

/** Matches the FAB this replaces, so nothing shifts at the corner. */
const LAUNCHER_SIZE = "3.5rem";
const EDGE = "2rem";

/**
 * The same mark the drawer's rail shows.
 *
 * A generic speech bubble said "a chat lives here" and nothing else; the brand
 * mark says *whose* assistant it is, and it is already the app's own logo two
 * inches away in the drawer.
 */
import { BRAND_LOGO as KERMITS_LOGO } from "@/components/ui/brand";

export function AgentPopup() {
  const t = useTranslations("chat");
  const theme = useTheme();
  const router = useRouter();
  const { user } = useAuth();
  const { popupOpen, setPopupOpen } = useChat();
  const pathname = usePathname();

  const panelRef = useRef<HTMLDivElement>(null);
  const launcherRef = useRef<HTMLButtonElement>(null);

  // Below the tablet breakpoint the panel takes the screen: a 400px panel inset
  // 2rem from the corner of a 375px viewport is wider than the viewport.
  const fullScreen = useMediaQuery(theme.breakpoints.down("md"));

  /**
   * Above MUI's docked drawer (1200), not the FAB's old `zIndex: 99`.
   *
   * At 99 the panel rendered *under* the sidenav and the sticky navbar. That was
   * invisible for a small panel tucked in the corner, and immediately wrong for
   * the full-screen one, which has to cover both.
   */
  const zIndex = theme.zIndex.drawer + 2;

  // Escape closes.
  useEffect(() => {
    if (!popupOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        setPopupOpen(false);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [popupOpen, setPopupOpen]);

  /**
   * Closing returns focus to the launcher, or focus is left on a removed node and
   * the next Tab starts from the top of the document.
   *
   * It has to happen in an effect rather than in the handlers that close the
   * panel. The launcher only renders while the panel is shut, so at the moment
   * `setPopupOpen(false)` is called `launcherRef` is still null -- focusing there
   * silently did nothing. By the time this effect runs the button exists.
   *
   * Guarded by "was it open a moment ago", so this does not steal focus on first
   * mount or on every unrelated re-render.
   */
  const wasOpen = useRef(false);
  /** Set when the panel is closing because we are navigating to the full page. */
  const handingOff = useRef(false);
  useEffect(() => {
    if (wasOpen.current && !popupOpen && !handingOff.current) {
      launcherRef.current?.focus();
    }
    handingOff.current = false;
    wasOpen.current = popupOpen;
  }, [popupOpen]);

  /**
   * Close on a genuine press outside the assistant and any overlay it owns.
   *
   * `ChatHistoryMenu` is a descendant in React, but MUI portals its paper under
   * `document.body`. DOM containment alone therefore calls a history-row press
   * "outside" and unmounts the popup before the selected transcript can remain
   * visible. Owned overlays carry a stable marker class, making the boundary
   * explicit instead of relying on where a component library mounts a portal.
   */
  useEffect(() => {
    if (!popupOpen || fullScreen) return;

    const onPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (panelRef.current?.contains(target)) return;

      const element =
        target instanceof Element ? target : target.parentElement;
      if (element?.closest(".tf26-agent-popup-owned-overlay")) return;

      setPopupOpen(false);
    };

    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [popupOpen, fullScreen, setPopupOpen]);

  // Signed out, there is nobody to ask on behalf of. The auth pages render
  // outside this shell anyway; this covers the moment before a redirect lands.
  if (!user) return null;

  /**
   * Not on /chat.
   *
   * The page *is* the assistant, so a floating button to open a smaller copy of
   * it on top of itself offers nothing -- and the panel, opened there, covers the
   * conversation it duplicates. Rendered on every other page, which is the whole
   * point of it.
   *
   * `usePathname` comes from the next-intl navigation helpers, so this is the
   * locale-stripped path and matches under both /chat and /en/chat.
   */
  if (pathname === "/chat") return null;

  return (
    <>
      {!popupOpen && (
        <VuiBox
          component="button"
          type="button"
          ref={launcherRef}
          onClick={() => setPopupOpen(true)}
          aria-label={t("open")}
          title={t("open")}
          display="flex"
          alignItems="center"
          justifyContent="center"
          sx={{
            position: "fixed",
            right: EDGE,
            bottom: EDGE,
            zIndex,
            width: LAUNCHER_SIZE,
            height: LAUNCHER_SIZE,
            padding: 0,
            cursor: "pointer",
            borderRadius: "50%",
            // A neutral disc, not the brand blue: the mark is full-colour and
            // already carries the brand, and blue-on-blue flattened it.
            backgroundColor: "var(--card)",
            border: "1px solid var(--border)",
            boxShadow: "0 4px 16px rgb(0 0 0 / 0.18)",
            transition: "transform 150ms ease, border-color 150ms ease",
            "&:hover": { borderColor: "var(--ring)", transform: "scale(1.05)" },
            "&:focus-visible": {
              outline: "2px solid var(--ring)",
              outlineOffset: 3,
            },
          }}
        >
          {/* `height`/`width` as HTML attributes only take bare numbers, so the
              size goes through `style`; `objectFit` keeps the mark centred in the
              circle rather than stretched to fill it.
              A plain <img>, as the drawer's own mark is: this is a 40px local PNG,
              and next/image would add a loader and a layout wrapper to it for no
              gain. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={KERMITS_LOGO}
            alt=""
            style={{
              display: "block",
              height: "32px",
              width: "32px",
              objectFit: "contain",
            }}
          />
        </VuiBox>
      )}

      {popupOpen && (
        <VuiBox
          ref={panelRef}
          role="dialog"
          aria-modal={fullScreen ? "true" : "false"}
          aria-label={t("title")}
          display="flex"
          flexDirection="column"
          sx={{
            position: "fixed",
            zIndex,
            backgroundColor: "var(--background)",
            overflow: "hidden",
            ...(fullScreen
              ? { inset: 0, borderRadius: 0, border: "none" }
              : {
                  border: "1px solid var(--border)",
                  right: EDGE,
                  bottom: EDGE,
                  width: 420,
                  // Tall enough to hold a table, short enough to leave the page
                  // behind it visible — capped by the viewport so it cannot run
                  // off the top on a short window.
                  height: "min(620px, calc(100vh - 6rem))",
                  borderRadius: "var(--radius)",
                }),
          }}
        >
          <VuiBox
            display="flex"
            alignItems="center"
            justifyContent="space-between"
            gap={1}
            px={2}
            py={1.5}
            // No rule under the header. The panel is small enough that the
            // wordmark and its two buttons already read as a header, and a line
            // across a 420px card just cuts it in half.
            sx={{ flexShrink: 0 }}
          >
            {/*
              The wordmark, not the page title. `t("title")` is still the dialog's
              accessible name on the wrapper above: "Assistant" says what the dialog
              *is*, which is what a screen reader needs, and a brand name would not.
            */}
            <BrandWordmark>{BRAND_AI}</BrandWordmark>

            <VuiBox
              display="flex"
              alignItems="center"
              gap={0.5}
              // Named so the history menu can line its right edge up with the
              // close button rather than with the button that opened it, which
              // left it floating in the middle of the panel.
              data-panel-header-actions=""
            >
              {/* The same control the page header uses, rather than a second
                  new-chat button beside a popup-only one. It brings the past
                  conversations with it: the panel could start a chat but never
                  return to one, so anything opened here was only reachable by
                  expanding to /chat first. Its menu renders in a portal, so a
                  420px panel does not clip it. */}
              <ChatHistoryMenu />
              <HeaderButton
                label={t("expand")}
                onClick={() => {
                  // Close first, so returning from /chat does not find the panel
                  // still open on top of the page it expanded into. Flagged as a
                  // hand-off so the refocus effect above leaves focus alone --
                  // /chat focuses its own composer, and pulling focus back to the
                  // launcher would undo that.
                  handingOff.current = true;
                  setPopupOpen(false);
                  router.push("/chat");
                }}
              >
                <Maximize2 size={16} />
              </HeaderButton>
              <HeaderButton
                label={t("close")}
                onClick={() => setPopupOpen(false)}
              >
                <X size={16} />
              </HeaderButton>
            </VuiBox>
          </VuiBox>

          {/* `minHeight: 0` so the transcript scrolls inside the panel rather than
              stretching it past the viewport. */}
          <VuiBox flexGrow={1} sx={{ minHeight: 0 }}>
            {/* The compact composer, and no centred empty state: in a 420px panel
                a vertically-centred composer with a heading above it leaves almost
                no room for the answer. */}
            <ChatPanel autoFocus placeholder={t("placeholder")} />
          </VuiBox>
        </VuiBox>
      )}
    </>
  );
}

/** One of the panel header's icon buttons. */
function HeaderButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <VuiBox
      component="button"
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      display="flex"
      alignItems="center"
      justifyContent="center"
      sx={{
        width: 30,
        height: 30,
        border: "none",
        padding: 0,
        cursor: "pointer",
        borderRadius: "var(--radius-full)",
        backgroundColor: "transparent",
        color: "var(--text-faint)",
        transition: "background-color 150ms ease, color 150ms ease",
        "&:hover": {
          backgroundColor: "var(--muted)",
          color: "var(--foreground)",
        },
        "&:focus-visible": {
          outline: "2px solid var(--ring)",
          outlineOffset: 2,
        },
      }}
    >
      {children}
    </VuiBox>
  );
}
