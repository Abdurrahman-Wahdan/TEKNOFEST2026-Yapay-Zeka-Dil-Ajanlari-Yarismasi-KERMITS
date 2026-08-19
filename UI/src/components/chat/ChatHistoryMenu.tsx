"use client";

import Menu from "@mui/material/Menu";
import { History, Plus, Trash2 } from "lucide-react";
import { useTranslations } from "next-intl";
import { useState } from "react";

import { VuiBox, VuiTypography } from "@/components/vision";
import { useChat } from "@/lib/chat/ChatProvider";

/**
 * The chat page's own header controls: start a new conversation, or reopen a past
 * one.
 *
 * A menu rather than a second column. The app already spends 96-250px on the
 * drawer, and a permanent history rail beside it would leave the transcript
 * squeezed between two lists on a laptop. The list is also short by nature -- one
 * person's own conversations -- so a menu is enough to scan.
 *
 * Not rendered in the popup. Its header already carries four controls in 420px,
 * and its expand button is the route to this page.
 */
export function ChatHistoryMenu() {
  const t = useTranslations("chat");
  const { history, activeId, newChat, openConversation, deleteConversation } =
    useChat();

  /**
   * The menu's anchor as state, not a ref.
   *
   * MUI wants the element itself, and reading `ref.current` during render is a
   * rules-of-hooks violation -- the render output would depend on a mutable value
   * React did not track. Captured from the click instead, which is also what
   * decides whether the menu is open.
   */
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);

  /**
   * The page title, not the button, is what the menu lines up with.
   *
   * Anchored to the button and right-aligned, a 280px paper opened *leftward* from
   * a control that sits just after the title -- straight over the drawer. Aligning
   * its left edge to the title instead starts it exactly where `KERMİTS AI` starts
   * and grows it rightward into the empty page, so it cannot reach the drawer at
   * any width: the title already begins after it.
   */
  const openMenu = (event: React.MouseEvent<HTMLElement>) => {
    const button = event.currentTarget;
    const toolbar = button.closest(".MuiToolbar-root");
    // The title element itself, so the paper's left edge lands on the `K` rather
    // than on the toolbar's padding 16px to its left. Two fallbacks, widest first:
    // the toolbar still guarantees no drawer collision, and the button always
    // exists.
    const title = toolbar?.querySelector(
      "[data-page-title]",
    ) as HTMLElement | null;
    setAnchor(title ?? (toolbar as HTMLElement | null) ?? button);
  };

  return (
    <VuiBox display="flex" alignItems="center" gap={0.5}>
      <HeaderControl label={t("newChat")} onClick={newChat}>
        <Plus size={18} />
      </HeaderControl>

      <HeaderControl
        label={t("history")}
        onClick={openMenu}
        // The count is the only hint that there is anything in here; without it
        // the button looks the same whether the list is empty or has twenty.
        badge={history.length > 0 ? history.length : undefined}
      >
        <History size={18} />
      </HeaderControl>

      <Menu
        open={Boolean(anchor)}
        anchorEl={anchor}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "left" }}
        transformOrigin={{ vertical: "top", horizontal: "left" }}
        // Keeps the paper off the viewport edge now that it opens rightward.
        marginThreshold={16}
        slotProps={{
          paper: {
            sx: {
              // The anchor is the whole toolbar, so the default drop clears its
              // full height; this brings the paper back up under the controls.
              mt: 0.5,
              minWidth: 280,
              maxWidth: 360,
              maxHeight: 420,
              backgroundColor: "var(--popover)",
              border: "1px solid var(--border)",
              backgroundImage: "none",
              borderRadius: "var(--radius-md)",
            },
          },
        }}
      >
        {history.length === 0 ? (
          <VuiBox px={2} py={1.5}>
            <VuiTypography variant="caption" color="text">
              {t("historyEmpty")}
            </VuiTypography>
          </VuiBox>
        ) : (
          history.map((conversation) => {
            const isActive = conversation.id === activeId;
            return (
              <VuiBox
                key={conversation.id}
                display="flex"
                alignItems="center"
                gap={1}
                px={1.5}
                py={1}
                sx={{
                  // Tinted rather than ticked: the row the user is already reading
                  // does not need a second affordance explaining itself.
                  backgroundColor: isActive ? "var(--muted)" : "transparent",
                  "&:hover": { backgroundColor: "var(--muted)" },
                  "&:hover .tf26-delete": { opacity: 1 },
                }}
              >
                <VuiBox
                  component="button"
                  type="button"
                  onClick={() => {
                    openConversation(conversation.id);
                    setAnchor(null);
                  }}
                  sx={{
                    flex: 1,
                    minWidth: 0,
                    border: "none",
                    background: "none",
                    padding: 0,
                    textAlign: "start",
                    cursor: "pointer",
                    fontFamily: "inherit",
                  }}
                >
                  <VuiTypography
                    variant="button"
                    fontWeight="regular"
                    color="white"
                    sx={{
                      display: "block",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {conversation.title}
                  </VuiTypography>
                </VuiBox>

                <VuiBox
                  component="button"
                  type="button"
                  className="tf26-delete"
                  onClick={() => deleteConversation(conversation.id)}
                  aria-label={t("deleteChat")}
                  title={t("deleteChat")}
                  display="flex"
                  alignItems="center"
                  justifyContent="center"
                  sx={{
                    width: 28,
                    height: 28,
                    flexShrink: 0,
                    border: "none",
                    background: "none",
                    padding: 0,
                    cursor: "pointer",
                    borderRadius: "var(--radius-full)",
                    color: "var(--control-ink)",
                    // Revealed on hover, but never hidden from the keyboard: an
                    // `opacity: 0` control is still focusable, so it has to show
                    // itself when focused or it is a trap.
                    opacity: 0,
                    transition: "opacity 150ms ease, color 150ms ease",
                    "&:hover": { color: "var(--danger)" },
                    "&:focus-visible": {
                      opacity: 1,
                      outline: "2px solid var(--ring)",
                      outlineOffset: 2,
                    },
                  }}
                >
                  <Trash2 size={15} />
                </VuiBox>
              </VuiBox>
            );
          })
        )}
      </Menu>
    </VuiBox>
  );
}

/** One of the two header buttons. */
function HeaderControl({
  label,
  onClick,
  children,
  badge,
}: {
  label: string;
  onClick: (event: React.MouseEvent<HTMLElement>) => void;
  children: React.ReactNode;
  badge?: number;
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
      gap={0.5}
      sx={{
        position: "relative",
        height: 32,
        px: 1,
        border: "none",
        cursor: "pointer",
        borderRadius: "var(--radius-full)",
        backgroundColor: "transparent",
        // The same grey every other quiet control in the app uses.
        color: "var(--control-ink)",
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
      {badge !== undefined && (
        <VuiBox
          component="span"
          sx={{
            fontSize: "0.6875rem",
            fontWeight: "var(--weight-medium)",
            lineHeight: 1,
            fontFamily: "inherit",
          }}
        >
          {badge}
        </VuiBox>
      )}
    </VuiBox>
  );
}
