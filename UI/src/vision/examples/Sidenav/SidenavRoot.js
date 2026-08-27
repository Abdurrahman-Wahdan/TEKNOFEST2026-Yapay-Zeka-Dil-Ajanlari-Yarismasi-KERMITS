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
import Drawer from "@mui/material/Drawer";
import { styled } from "@mui/material/styles";
import linearGradient from "assets/theme/functions/linearGradient";

import { SIDENAV_RAIL, SIDENAV_WIDTH } from "vision/sidenavWidths";

export default styled(Drawer)(({ theme, ownerState }) => {
  const { palette, boxShadows, transitions } = theme;
  const { transparentSidenav, miniSidenav, isPhone } = ownerState;

  const { transparent, gradients } = palette;
  const { xxl } = boxShadows;

  // One transition for both states, on `width` alone. The drawer used to slide
  // itself off-screen with `translateX(-20rem)` below xl and only become a rail
  // above it; now that the rail applies at every width there is nothing left to
  // translate, and animating the width is what makes the collapse read as the
  // panel narrowing rather than leaving.
  const widthTransition = transitions.create(["width", "background-color"], {
    easing: transitions.easing.sharp,
    duration: transitions.duration.shorter,
  });

  const surface = {
    boxShadow: transparentSidenav ? "none" : xxl,
    marginBottom: transparentSidenav ? 0 : "inherit",
    left: 0,
    transform: "translateX(0)",
    transition: widthTransition,
  };

  return {
    "& .MuiDrawer-paper": {
      boxShadow: xxl,
      border: "none",
      background: transparentSidenav
        ? transparent.main
        : linearGradient(gradients.sidenav.main, gradients.sidenav.state, gradients.sidenav.deg),
      backdropFilter: transparentSidenav ? "unset" : "blur(120px)",
      ...surface,
      // On a phone the drawer is a temporary overlay and always full width: a
      // 96px rail there would be a quarter of the screen spent on navigation,
      // and the overlay is dismissed rather than narrowed.
      width: isPhone ? SIDENAV_WIDTH : miniSidenav ? SIDENAV_RAIL : SIDENAV_WIDTH,
      // The labels are still in the DOM while collapsed — they fade and lose
      // their width rather than unmount — so the rail has to clip them instead
      // of scrolling sideways to reveal them.
      overflowX: "hidden",
    },
  };
});
