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

import { useEffect } from "react";

// Next routing, via the react-router shim in vision/router.js
import { useLocation } from "vision/router";

// prop-types is a library for typechecking of props.
import PropTypes from "prop-types";

// Vision UI Dashboard React components
import VuiBox from "components/VuiBox";

// Vision UI Dashboard React context
import { useVisionUIController, setLayout } from "context";
import { SIDENAV_GUTTER, SIDENAV_RAIL, SIDENAV_WIDTH } from "vision/sidenavWidths";

function DashboardLayout({ children }) {
  const [controller, dispatch] = useVisionUIController();
  const { miniSidenav } = controller;
  const { pathname } = useLocation();

  useEffect(() => {
    setLayout(dispatch, "dashboard");
  }, [pathname]);

  return (
    <VuiBox
      sx={({ breakpoints, transitions, functions: { pxToRem } }) => ({
        p: 3,
        position: "relative",

        // No longer gated on `xl`. The drawer is a rail at every width now
        // rather than sliding off-screen below 1440, so the content has to make
        // room for it everywhere -- otherwise the rail sits on top of the page.
        // The offset is derived from the drawer's own widths rather than the
        // pre-summed 120/274 this used to hardcode.
        marginLeft: pxToRem((miniSidenav ? SIDENAV_RAIL : SIDENAV_WIDTH) + SIDENAV_GUTTER),

        // Below `md` the drawer is a temporary overlay rather than a docked rail,
        // so there is nothing for the content to make room for -- it takes the
        // full width and the drawer floats over it when opened.
        [breakpoints.down("md")]: {
          marginLeft: 0,
          p: 2,
        },
        transition: transitions.create(["margin-left", "margin-right"], {
          easing: transitions.easing.easeInOut,
          duration: transitions.duration.standard,
        }),
      })}
    >
      {children}
    </VuiBox>
  );
}

// Typechecking props for the DashboardLayout
DashboardLayout.propTypes = {
  children: PropTypes.node.isRequired,
};

export default DashboardLayout;
