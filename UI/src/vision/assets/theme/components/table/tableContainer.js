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

  // The outer edges of every table in the app.
  //
  // The first column originally had its left padding forced to 0, leaving only
  // the Card's own padding between the content and the card edge — never enough
  // to read as intentional. 12px was the first correction and was still tight,
  // with the row text sitting close to the border on both sides.
  //
  // This is the single place that decides it, and it deliberately covers *both*
  // edges: setting only the left one is what left the last column's right
  // padding to whatever each table happened to pass, which is how a header on
  // 24px ended up above a cell on 8px. Inner gutters stay narrower so columns
  // still read as related; only the outer edges get the room.
  const edgePadding = pxToRem(24);

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
                paddingLeft: `${edgePadding} !important`,
              },
              "&:last-of-type": {
                paddingRight: `${edgePadding} !important`,
              },
            },
          },
        },
        "& .MuiTableBody-root": {
          "& tr": {
            "& td": {
              "&:first-of-type": {
                paddingLeft: `${edgePadding} !important`,
                "& .MuiBox-root": {
                  paddingLeft: `${edgePadding} !important`,
                },
              },
              "&:last-of-type": {
                paddingRight: `${edgePadding} !important`,
              },
            },
          },
        },
      },
    },
  };
};
