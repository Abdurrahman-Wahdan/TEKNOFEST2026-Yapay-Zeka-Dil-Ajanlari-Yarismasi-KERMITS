/*!

=========================================================
* Vision UI Free React - v1.0.0
=========================================================

* Product Page: https://www.creative-tim.com/product/vision-ui-free-react
* Copyright 2021 Creative Tim (https://www.creative-tim.com/)
* Licensed under MIT (https://github.com/creativetimofficial/vision-ui-free-react/blob/master LICENSE.md)

* Design and Coded by Simmmple & Creative Tim

=========================================================

* The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

*/

// @mui material components
import Fade from "@mui/material/Fade";

// Vision UI Dashboard React base styles
import typography from "assets/theme/base/typography";
import borders from "assets/theme/base/borders";

// Vision UI Dashboard React helper functions
import pxToRem from "assets/theme/functions/pxToRem";
import rgba from "assets/theme/functions/rgba";

// Takes `colors` so the theme can be rebuilt per light/dark mode; it used to
// read a module-scope object frozen at import time.
export default (colors) => {
  const { black, onImage } = colors;
  const { size, fontWeightRegular } = typography(colors);
  const { borderRadius } = borders(colors);

  return {
    defaultProps: {
      arrow: true,
      TransitionComponent: Fade,
    },

    styleOverrides: {
      tooltip: {
        maxWidth: pxToRem(240),
        // The alpha belongs on the background alone, not the whole element:
        // CSS `opacity` used to fade the text along with it, which left a
        // white-on-near-black tooltip reading as barely more than a grey
        // smudge. `rgba` keeps the box translucent and the text fully
        // legible.
        backgroundColor: rgba(black.main, 0.92),
        // Not `light.main` -- that token is `muted`, a dark surface colour
        // in this palette, not a readable light text colour. The tooltip's
        // own background is always dark regardless of the app's light/dark
        // mode, so its text needs the same mode-independent "ink" token the
        // rest of the app uses over a fixed-dark surface (`onImage`), not one
        // of the two that swap with the theme.
        color: onImage.main,
        fontSize: size.xs,
        fontWeight: fontWeightRegular,
        textAlign: "center",
        borderRadius: borderRadius.md,
        padding: `${pxToRem(8)} ${pxToRem(12)}`,
      },

      arrow: {
        color: rgba(black.main, 0.92),
      },
    },
  };
};
