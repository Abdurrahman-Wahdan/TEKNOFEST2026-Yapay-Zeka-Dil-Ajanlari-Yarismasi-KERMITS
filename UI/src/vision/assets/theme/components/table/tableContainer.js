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

// Takes `colors` so the theme can be rebuilt per light/dark mode; it used to
// read a module-scope object frozen at import time.
export default (colors) => {
  const { transparent } = colors;
  const { xxl } = boxShadows(colors);
  const { borderRadius } = borders(colors);

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
                paddingLeft: "0px !important",
              },
            },
          },
        },
        "& .MuiTableBody-root": {
          "& tr": {
            "& td": {
              "&:first-of-type": {
                paddingLeft: "0px !important",
                "& .MuiBox-root": {
                  paddingLeft: "0px !important",
                },
              },
            },
          },
        },
      },
    },
  };
};
