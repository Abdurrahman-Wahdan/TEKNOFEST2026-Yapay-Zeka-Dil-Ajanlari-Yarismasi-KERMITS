"use client";

import IconButton from "@mui/material/IconButton";
import Tooltip from "@mui/material/Tooltip";
import { PanelLeft, PanelLeftClose } from "lucide-react";
import { useTranslations } from "next-intl";
import PropTypes from "prop-types";

/**
 * The drawer's collapse/expand button.
 *
 * Rendered twice by `Sidenav`, and that is deliberate rather than duplication:
 * expanded it sits at the right of the header next to the wordmark, collapsed it
 * takes the logo's place in the centre of the rail. Two positions, one control,
 * so it is a component rather than a copied block.
 *
 * A real `<button>` with a label and `aria-expanded` — it replaces a `div` that
 * carried an `onClick` and nothing else: no role, no accessible name, and no way
 * to reach it from the keyboard.
 *
 * lucide, not MUI's `<Icon>`, per the note in `components/ui/ThemeGlyph.tsx`
 * that lucide is the app's set. The glyph shows the panel with its rail, which
 * is the same metaphor the label spells out in words.
 */
export default function SidenavToggle({ miniSidenav, onClick }) {
  const t = useTranslations("nav");
  const label = miniSidenav ? t("expandSidebar") : t("collapseSidebar");
  const Glyph = miniSidenav ? PanelLeft : PanelLeftClose;

  return (
    <Tooltip title={label} placement="right" disableInteractive>
      <IconButton
        size="small"
        color="inherit"
        onClick={onClick}
        aria-label={label}
        aria-expanded={!miniSidenav}
        sx={({ palette }) => ({
          color: palette.text.main,
          "&:hover": { color: palette.white.main },
        })}
      >
        <Glyph size={20} aria-hidden="true" />
      </IconButton>
    </Tooltip>
  );
}

SidenavToggle.propTypes = {
  miniSidenav: PropTypes.bool.isRequired,
  onClick: PropTypes.func.isRequired,
};
