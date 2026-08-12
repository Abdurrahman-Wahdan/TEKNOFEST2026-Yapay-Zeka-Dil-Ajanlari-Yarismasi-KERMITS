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
import typography from "assets/theme/base/typography";

// Vision UI Dashboard React helper functions
import pxToRem from "assets/theme/functions/pxToRem";

// Takes `colors` so the theme can be rebuilt per light/dark mode; it used to
// read a module-scope object frozen at import time.
export default (colors) => {
  const { size, fontWeightRegular } = typography(colors);
  const { grey, dark, text } = colors;

  return {
    styleOverrides: {
      label: {
        marginTop: `${pxToRem(8)} !important`,
        fontWeight: fontWeightRegular,
        fontSize: size.md,
        color: grey[300],

        "&.Mui-active": {
          fontWeight: `${fontWeightRegular} !important`,
          color: `${dark.main} !important`,
        },

        "&.Mui-completed": {
          fontWeight: `${fontWeightRegular} !important`,
          color: `${text.main} !important`,
        },
      },
    },
  };
};
