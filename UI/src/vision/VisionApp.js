"use client";

import { useEffect, useMemo, useState } from "react";

// @mui material components
import { ThemeProvider } from "@mui/material/styles";
import CssBaseline from "@mui/material/CssBaseline";

// Vision UI Dashboard React example components
import Sidenav from "examples/Sidenav";
// The Configurator drawer (theme presets, "free download"/"documentation"
// panel) is deliberately not mounted. The component is kept intact at
// `examples/Configurator` so it can be brought back: re-add the import, render
// <Configurator /> beside the Sidenav, and restore the button below.
import { ThemeToggleFab } from "components/VuiThemeToggle";

// Vision UI Dashboard React themes
import createVisionTheme from "assets/theme";
import createVisionThemeRTL from "assets/theme/theme-rtl";

// RTL plugins
import rtlPlugin from "stylis-plugin-rtl";
import { CacheProvider } from "@emotion/react";
import createCache from "@emotion/cache";

// Vision UI Dashboard React routes
import routes from "routes";

// Vision UI Dashboard React contexts
import { useVisionUIController, setMiniSidenav } from "context";

// The app-wide light/dark state, shared with the sign-in screen's toggle.
import { useTheme as useColorMode } from "@/lib/theme";

/**
 * The template's App.js, as a Next.js layout.
 *
 * Everything the original does is preserved — the theme, the RTL emotion cache,
 * the mini-sidenav hover behaviour, the configurator and its floating button.
 * The one thing removed is react-router's `<Switch>`: Next resolves pages from
 * the filesystem, so the routed content arrives as `children` instead.
 */
export default function VisionApp({ children }) {
  const [controller, dispatch] = useVisionUIController();
  const { miniSidenav, direction, layout, sidenavColor } = controller;
  const [onMouseEnter, setOnMouseEnter] = useState(false);
  const [rtlCache, setRtlCache] = useState(null);

  // Rebuild the MUI theme whenever the mode changes. This is what makes the
  // light/dark toggle actually reach the dashboard: the theme is derived from
  // the mode rather than created once at import.
  const { theme: mode } = useColorMode();
  const theme = useMemo(() => createVisionTheme(mode), [mode]);
  const themeRTL = useMemo(() => createVisionThemeRTL(mode), [mode]);

  // Cache for the rtl
  useMemo(() => {
    const cacheRtl = createCache({
      key: "rtl",
      stylisPlugins: [rtlPlugin],
    });

    setRtlCache(cacheRtl);
  }, []);

  // Open sidenav when mouse enter on mini sidenav
  const handleOnMouseEnter = () => {
    if (miniSidenav && !onMouseEnter) {
      setMiniSidenav(dispatch, false);
      setOnMouseEnter(true);
    }
  };

  // Close sidenav when mouse leave mini sidenav
  const handleOnMouseLeave = () => {
    if (onMouseEnter) {
      setMiniSidenav(dispatch, true);
      setOnMouseEnter(false);
    }
  };

  // Setting the dir attribute for the body element
  useEffect(() => {
    document.body.setAttribute("dir", direction);
  }, [direction]);

  const shell = (
    <>
      <CssBaseline />
      {layout === "dashboard" && (
        <>
          <Sidenav
            color={sidenavColor}
            brand=""
            brandName="KERMİTS"
            routes={routes}
            onMouseEnter={handleOnMouseEnter}
            onMouseLeave={handleOnMouseLeave}
          />
          {/* Where the Configurator's settings button used to sit. */}
          <ThemeToggleFab />
        </>
      )}
      {children}
    </>
  );

  return direction === "rtl" ? (
    <CacheProvider value={rtlCache}>
      <ThemeProvider theme={themeRTL}>{shell}</ThemeProvider>
    </CacheProvider>
  ) : (
    <ThemeProvider theme={theme}>{shell}</ThemeProvider>
  );
}
