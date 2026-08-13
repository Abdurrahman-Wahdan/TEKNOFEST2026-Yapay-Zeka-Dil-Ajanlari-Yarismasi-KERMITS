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

// Vision UI Dashboard React Base Styles
import typography from "assets/theme/base/typography";
import borders from "assets/theme/base/borders";

// Vision UI Dashboard  helper functions
import pxToRem from "assets/theme/functions/pxToRem";

// Takes `colors` so the theme can be rebuilt per light/dark mode; it used to
// read a module-scope object frozen at import time.
export default (colors) => {
  const { dark, grey, inputColors } = colors;
  const { size, fontWeightRegular } = typography(colors);
  const { borderWidth, borderRadius } = borders(colors);

  return {
    styleOverrides: {
      root: {
        display: "grid !important",
        placeItems: "center !important",
        width: "100% !important",
        height: "auto !important",
        padding: `${pxToRem(8)} ${pxToRem(12)}`,
        fontSize: `${size.sm} !important`,
        fontWeight: `${fontWeightRegular} !important`,
        lineHeight: "1.4 !important",
        color: `${grey[700]} !important`,
        backgroundColor: `${inputColors.backgroundColor} !important`,
        backgroundClip: "padding-box !important",
        border: `${borderWidth[1]} solid ${inputColors.borderColor.main}`,
        appearance: "none !important",
        borderRadius: borderRadius.md,
        transition: "box-shadow 150ms ease, border-color 150ms ease, padding 150ms ease !important",
      },

      input: {
        width: "100% !important",
        height: `${pxToRem(22)}`,
        padding: "0 !important",

        "&::-webkit-input-placeholder": {
          color: `${dark.main} !important`,
        },
      },
    },
  };
};
