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
import borders from "assets/theme/base/borders";
import boxShadows from "assets/theme/base/boxShadows";

// Takes `colors` so the theme can be rebuilt per light/dark mode.
export default (colors) => {
  const { borderRadius } = borders(colors);
  const { xxl } = boxShadows(colors);

  return {
    styleOverrides: {
      paper: {
        borderRadius: borderRadius.lg,
        boxShadow: xxl,
      },

      paperFullScreen: {
        borderRadius: 0,
      },
    },
  };
};
