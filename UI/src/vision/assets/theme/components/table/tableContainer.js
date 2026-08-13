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
import borders from "assets/theme/base/borders";
import pxToRem from "assets/theme/functions/pxToRem";

// Takes `colors` so the theme can be rebuilt per light/dark mode; it used to
// read a module-scope object frozen at import time.
export default (colors) => {
  const { transparent } = colors;
  const { xxl } = boxShadows(colors);
  const { borderRadius } = borders(colors);

  // The first column had its left padding forced to 0 here, so the only
  // thing standing between its content and the card edge was the Card's own
  // padding — never enough to read as intentional breathing room, and why
  // the row icons/text sat flush against it. A modest inset instead, not the
  // full per-column padding the other columns get (that would over-indent
  // the first column relative to the header text it needs to align under).
  const firstColumnPadding = pxToRem(12);

  return {
    styleOverrides: {
      root: {
        backgroundColor: transparent.main,
        boxShadow: xxl,
        borderRadius: borderRadius.xl,
        "& thead": {
          "& tr": {
            "& th": {
              "&:first-of-type": {
                paddingLeft: `${firstColumnPadding} !important`,
              },
            },
          },
        },
        "& .MuiTableBody-root": {
          "& tr": {
            "& td": {
              "&:first-of-type": {
                paddingLeft: `${firstColumnPadding} !important`,
                "& .MuiBox-root": {
                  paddingLeft: `${firstColumnPadding} !important`,
                },
              },
            },
          },
        },
      },
    },
  };
};
