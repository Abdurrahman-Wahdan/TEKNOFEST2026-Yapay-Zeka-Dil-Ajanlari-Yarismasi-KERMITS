"use client";

import Card from "@mui/material/Card";
import { ChevronDown, ChevronUp } from "lucide-react";
import { useTranslations } from "next-intl";
import { useId, useState, type ReactNode } from "react";

import { VuiBox, VuiTypography } from "@/components/vision";

/**
 * A card whose body the user can fold away, leaving only its heading.
 *
 * This is the card frame plus one control, not a new surface: it renders the
 * same MUI `Card` every other block on the page renders, so a collapsible card
 * and a fixed one sit in the same column without looking like two components.
 * Adopting it anywhere else is a wrapper swap — replace `<Card>` with
 * `<CollapsibleCard title={…}>` and delete the hand-rolled heading, since this
 * draws the title and description in the same `lg`/`caption` pair the cards
 * already use.
 *
 * Only the catalogue uses it today. It is deliberately unopinionated about
 * *what* is being folded so the results table, the not-ranked list, or any
 * future widget can take it without the component learning about them.
 *
 * `description` stays visible while collapsed, on purpose: a folded card that
 * shows nothing but a title makes the user open it to find out whether it is
 * worth opening. The sentence under the heading is what tells them.
 */
export function CollapsibleCard({
  title,
  description,
  actions,
  defaultCollapsed = false,
  children,
}: {
  title: ReactNode;
  /** Kept on screen when collapsed — see above. */
  description?: ReactNode;
  /** Extra controls for the header row, drawn left of the fold button. */
  actions?: ReactNode;
  defaultCollapsed?: boolean;
  children: ReactNode;
}) {
  const t = useTranslations("common");
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const bodyId = useId();
  const label = collapsed ? t("expand") : t("minimize");

  return (
    <Card>
      <VuiBox display="flex" alignItems="flex-start" justifyContent="space-between" gap="12px">
        <VuiBox>
          <VuiBox mb={description ? "8px" : 0}>
            <VuiTypography variant="lg" color="white">
              {title}
            </VuiTypography>
          </VuiBox>
          {description && (
            <VuiTypography variant="caption" color="text">
              {description}
            </VuiTypography>
          )}
        </VuiBox>

        <VuiBox display="flex" alignItems="center" gap="8px" flexShrink={0}>
          {actions}
          {/* Quiet by default and lit on hover, the same treatment the table's
              own row control gets: a header button that competes with the
              heading beside it turns every card into a toolbar. */}
          <VuiBox
            component="button"
            type="button"
            onClick={() => setCollapsed((value) => !value)}
            aria-expanded={!collapsed}
            aria-controls={bodyId}
            aria-label={label}
            title={label}
            display="inline-flex"
            alignItems="center"
            justifyContent="center"
            sx={{
              width: 28,
              height: 28,
              border: "none",
              padding: 0,
              cursor: "pointer",
              borderRadius: "var(--radius-full)",
              backgroundColor: "transparent",
              color: "var(--control-ink)",
              transition: "background-color 150ms ease, color 150ms ease",
              "&:hover": { backgroundColor: "var(--muted)", color: "var(--foreground)" },
              "&:focus-visible": { outline: "2px solid var(--ring)", outlineOffset: 2 },
            }}
          >
            {collapsed ? (
              <ChevronDown size={17} aria-hidden="true" />
            ) : (
              <ChevronUp size={17} aria-hidden="true" />
            )}
          </VuiBox>
        </VuiBox>
      </VuiBox>

      {/* Unmounted rather than hidden: a collapsed card should cost nothing to
          have on the page, and `display: none` still leaves a full table in
          the DOM for the browser and the screen reader to walk. Component
          state inside the body does not survive a fold, which is the right
          trade for a disclosure -- anything that must outlive it belongs to
          the caller, the way the catalogue's bank picker lives in Comparator. */}
      {!collapsed && <VuiBox id={bodyId}>{children}</VuiBox>}
    </Card>
  );
}
