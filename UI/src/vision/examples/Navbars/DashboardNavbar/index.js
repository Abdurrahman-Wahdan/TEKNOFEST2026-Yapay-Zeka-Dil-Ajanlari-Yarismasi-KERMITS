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

import { useState, useEffect } from "react";

// react-router components
import { useLocation, Link } from "vision/router";

// prop-types is a library for typechecking of props.
import PropTypes from "prop-types";

// @material-ui core components
import AppBar from "@mui/material/AppBar";
import Toolbar from "@mui/material/Toolbar";
import IconButton from "@mui/material/IconButton";
import Menu from "@mui/material/Menu";
import Icon from "@mui/material/Icon";

// Vision UI Dashboard React components
import VuiBox from "components/VuiBox";
import { ThemeToggleIconButton } from "components/VuiThemeToggle";

import { LocaleToggleIconButton } from "@/components/ui/LocaleToggle";

// Vision UI Dashboard React example components
import Breadcrumbs from "examples/Breadcrumbs";
import NotificationItem from "examples/Items/NotificationItem";

// Custom styles for DashboardNavbar
import {
  navbar,
  navbarContainer,
  navbarRow,
  navbarIconButton,
} from "examples/Navbars/DashboardNavbar/styles";

// Vision UI Dashboard React context
import {
  useVisionUIController,
  setTransparentNavbar,
} from "context";

// Images
// Served from /public rather than imported: Next resolves a static image
// import to a StaticImageData object, and this template interpolates the
// value straight into CSS url(...) — which would emit [object Object].
const team2 = "/vision/images/team-2.jpg";
// Served from /public rather than imported: Next resolves a static image
// import to a StaticImageData object, and this template interpolates the
// value straight into CSS url(...) — which would emit [object Object].
const logoSpotify = "/vision/images/small-logos/logo-spotify.svg";
function DashboardNavbar({ absolute = false, light = false, isMini = false }) {
  const [navbarType, setNavbarType] = useState();
  const [controller, dispatch] = useVisionUIController();
  const { transparentNavbar, fixedNavbar } = controller;
  const [openMenu, setOpenMenu] = useState(false);
  const route = useLocation().pathname.split("/").slice(1);

  useEffect(() => {
    // Setting the navbar type
    if (fixedNavbar) {
      setNavbarType("sticky");
    } else {
      setNavbarType("static");
    }

    // A function that sets the transparent state of the navbar.
    function handleTransparentNavbar() {
      setTransparentNavbar(dispatch, (fixedNavbar && window.scrollY === 0) || !fixedNavbar);
    }

    /** 
     The event listener that's calling the handleTransparentNavbar function when 
     scrolling the window.
    */
    window.addEventListener("scroll", handleTransparentNavbar);

    // Call the handleTransparentNavbar function to set the state with the initial value.
    handleTransparentNavbar();

    // Remove event listener on cleanup
    return () => window.removeEventListener("scroll", handleTransparentNavbar);
  }, [dispatch, fixedNavbar]);

  const handleOpenMenu = (event) => setOpenMenu(event.currentTarget);
  const handleCloseMenu = () => setOpenMenu(false);

  // Render the notifications menu
  const renderMenu = () => (
    <Menu
      anchorEl={openMenu}
      anchorReference={null}
      anchorOrigin={{
        vertical: "bottom",
        horizontal: "left",
      }}
      open={Boolean(openMenu)}
      onClose={handleCloseMenu}
      sx={{ mt: 2 }}
    >
      <NotificationItem
        image={<img src={team2} alt="person" />}
        title={["New message", "from Laur"]}
        date="13 minutes ago"
        onClick={handleCloseMenu}
      />
      <NotificationItem
        image={<img src={logoSpotify} alt="person" />}
        title={["New album", "by Travis Scott"]}
        date="1 day"
        onClick={handleCloseMenu}
      />
      <NotificationItem
        color="text"
        image={
          <Icon fontSize="small" sx={{ color: ({ palette: { white } }) => white.main }}>
            payment
          </Icon>
        }
        title={["", "Payment successfully completed"]}
        date="2 days"
        onClick={handleCloseMenu}
      />
    </Menu>
  );

  return (
    <AppBar
      position={absolute ? "absolute" : navbarType}
      color="inherit"
      sx={(theme) => navbar(theme, { transparentNavbar, absolute, light })}
    >
      <Toolbar sx={(theme) => navbarContainer(theme)}>
        <VuiBox color="inherit" mb={{ xs: 1, md: 0 }} sx={(theme) => navbarRow(theme, { isMini })}>
          <Breadcrumbs icon="home" title={route[route.length - 1]} route={route} light={light} />
        </VuiBox>
        {isMini ? null : (
          <VuiBox sx={(theme) => navbarRow(theme, { isMini })}>
            {/*
              The search field is deliberately not rendered. `VuiInput` is
              untouched at `components/VuiInput`, so bringing it back is just
              restoring this block:

                <VuiBox pr={1}>
                  <VuiInput
                    placeholder="Type here..."
                    icon={{ component: "search", direction: "left" }}
                    sx={({ breakpoints }) => ({
                      [breakpoints.down("sm")]: { maxWidth: "80px" },
                      [breakpoints.only("sm")]: { maxWidth: "80px" },
                      backgroundColor: "info.main !important",
                    })}
                  />
                </VuiBox>
            */}
            {/*
              Counted from the RIGHT-HAND EDGE inwards: profile, then the
              light/dark switch, then the language switch, then the drawer
              toggle, then notifications furthest from the edge. The row is
              flush right, so source order runs the other way and this block
              reads bottom-up: notifications first in the markup, profile last.
              Reorder by moving whole entries, and re-check against the edge
              rather than against the source.
            */}
            <VuiBox color={light ? "white" : "inherit"}>
              <IconButton
                size="small"
                color="inherit"
                sx={navbarIconButton}
                aria-controls="notification-menu"
                aria-haspopup="true"
                variant="contained"
                onClick={handleOpenMenu}
              >
                <Icon
                  sx={({ palette: { dark, white } }) => ({
                    color: light ? white.main : dark.main,
                  })}
                >
                  notifications
                </Icon>
              </IconButton>
              {/* No drawer toggle here. The drawer collapses to a rail that is
                  always on screen and carries its own toggle in its header --
                  visible expanded, and revealed on hover or focus when
                  collapsed -- so a second control in the navbar was pointing at
                  something the user could already see and reach. Counted from
                  the right edge the row is now profile, theme, language,
                  notifications. */}
              <LocaleToggleIconButton sx={navbarIconButton} />
              {/* Was the Configurator's settings gear. The Configurator is no
                  longer mounted, so this would have been a dead button; it is
                  the light/dark switch instead. */}
              <ThemeToggleIconButton sx={navbarIconButton} />
              {/*
                Reaching the dashboard already means signed in, so this is the
                profile link rather than the template's "Sign in" call to
                action. Icon only — the label was the sign-in prompt.
              */}
              <Link to="/profile">
                <IconButton sx={navbarIconButton} size="small" aria-label="Your profile">
                  <Icon
                    sx={({ palette: { dark, white } }) => ({
                      color: light ? white.main : dark.main,
                    })}
                  >
                    account_circle
                  </Icon>
                </IconButton>
              </Link>
              {renderMenu()}
            </VuiBox>
          </VuiBox>
        )}
      </Toolbar>
    </AppBar>
  );
}

// Typechecking props for the DashboardNavbar
DashboardNavbar.propTypes = {
  absolute: PropTypes.bool,
  light: PropTypes.bool,
  isMini: PropTypes.bool,
};

export default DashboardNavbar;
