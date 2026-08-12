"use client";

import IconButton from "@mui/material/IconButton";

import VuiBox from "components/VuiBox";

import { ThemeGlyph } from "@/components/ui/ThemeGlyph";
import { useTheme } from "@/lib/theme";

/**
 * Light/dark switch, in the two shapes the template needs.
 *
 * It replaces the Configurator's entry points — the floating gear at the bottom
 * right and the gear in the navbar. The Configurator itself is kept in
 * `examples/Configurator` and simply not mounted, so it can come back.
 *
 * The state comes from `@/lib/theme` and the icon from `ThemeGlyph` — both
 * shared with the sign-in and sign-up screens, so every theme switch in the app
 * looks the same and stays in step. Icons come from lucide-react, not MUI's
 * <Icon>, so the app reads as one icon family.
 */

/** The floating circular button, matching the Configurator button it replaces. */
export function ThemeToggleFab() {
  const { theme, toggle } = useTheme();
  const next = theme === "dark" ? "light" : "dark";

  return (
    <VuiBox
      display="flex"
      justifyContent="center"
      alignItems="center"
      width="3.5rem"
      height="3.5rem"
      bgColor="info"
      shadow="sm"
      borderRadius="50%"
      position="fixed"
      right="2rem"
      bottom="2rem"
      zIndex={99}
      color="dark"
      sx={{ cursor: "pointer" }}
      onClick={toggle}
      role="button"
      aria-label={`Switch to ${next} mode`}
      title={`Switch to ${next} mode`}
    >
      {/* The same lucide glyph the sign-in and sign-up screens use. */}
      <ThemeGlyph theme={theme} size={24} />
    </VuiBox>
  );
}

/** The navbar variant, sitting where the settings gear used to. */
export function ThemeToggleIconButton({ sx }) {
  const { theme, toggle } = useTheme();
  const next = theme === "dark" ? "light" : "dark";

  return (
    <IconButton
      size="small"
      color="inherit"
      sx={sx}
      onClick={toggle}
      aria-label={`Switch to ${next} mode`}
      title={`Switch to ${next} mode`}
    >
      <ThemeGlyph theme={theme} />
    </IconButton>
  );
}

export default ThemeToggleFab;
