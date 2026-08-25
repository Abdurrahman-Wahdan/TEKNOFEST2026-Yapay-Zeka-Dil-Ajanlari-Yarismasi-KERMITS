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
//
// The bottom-right corner now belongs to the assistant. `ThemeToggleFab` is
// still exported from `components/VuiThemeToggle` and can come back the same
// way, but nothing was lost by moving it out: the theme toggle it provided is
// also in the navbar (`examples/Navbars/DashboardNavbar`), which is where every
// other page-level control lives anyway.
import { AgentPopup } from "@/components/chat/AgentPopup";
import { SelectionReply } from "@/components/chat/SelectionReply";
import { ReportToasts } from "@/components/widgets/ReportToasts";

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
import { useVisionUIController } from "context";

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
  const { direction, layout, sidenavColor } = controller;
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

  // The template's hover-to-expand handlers used to live here and are gone.
  // They could never fire -- their `if (miniSidenav)` guard only passed below
  // 1200px, where the drawer was translated off-screen and so could not be
  // hovered -- and the behaviour is not what we want anyway: `Sidenav` now
  // tracks hover itself, and uses it only to reveal the expand button. A rail
  // that expands on hover moves the page out from under the pointer.

  // Setting the dir attribute for the body element
  useEffect(() => {
    document.body.setAttribute("dir", direction);
  }, [direction]);

  const shell = (
    <>
      <CssBaseline />
      {layout === "dashboard" && (
        <>
          <Sidenav color={sidenavColor} brand="" brandName="KERMİTS" routes={routes} />
          {/* Where the Configurator's settings button, and then the theme
              toggle, used to sit. */}
          <AgentPopup />
          {/* One selection listener for the whole dashboard, rather than one per
              page. It renders nothing until there is a selection to act on. */}
          <SelectionReply />
          {/* One socket for the whole dashboard, for the same reason. A report
              lands while the user is reading something else -- that is the
              entire point of it -- so the thing that announces it cannot live
              on the page they would have found out on anyway. */}
          <ReportToasts />
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
