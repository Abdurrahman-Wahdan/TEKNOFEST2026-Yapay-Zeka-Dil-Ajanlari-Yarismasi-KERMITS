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

// Vision UI Dashboard React base styles
import boxShadows from "assets/theme/base/boxShadows";
import typography from "assets/theme/base/typography";
import borders from "assets/theme/base/borders";
import linearGradient from "assets/theme/functions/linearGradient";

// Vision UI Dashboard React helper functions
import pxToRem from "assets/theme/functions/pxToRem";

// Takes `colors` so the theme can be rebuilt per light/dark mode; it used to
// read a module-scope object frozen at import time.
export default (colors) => {
  const { lg } = boxShadows(colors);
  const { size } = typography(colors);
  const { white, borderCol, gradients, transparent } = colors;
  const { borderRadius, borderWidth } = borders(colors);

  return {
    defaultProps: {
      disableAutoFocusItem: true,
    },

    styleOverrides: {
      "& .MuiIcon-root": {
        stroke: white.main,
      },
      paper: {
        minWidth: pxToRem(160),
        boxShadow: lg,
        padding: `0 !important`,
        fontSize: size.sm,
        color: white.main,
        textAlign: "left",
        border: `${borderWidth[1]} solid ${borderCol.navbar}`,
        borderRadius: borderRadius.md,
      },
      list: {
        background: linearGradient(gradients.menu.main, gradients.menu.state, gradients.menu.deg),
        "& .MuiMenuItem-root": {
          "& .MuiBox-root .MuiTypography-root": {
            color: white.main,
          },
          "&:hover": {
            background: transparent.main,
          },
        },
      },
    },
  };
};
