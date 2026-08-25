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
import Icon from "@mui/material/Icon";
import Badge from "@mui/material/Badge";

// Vision UI Dashboard React components
import VuiBox from "components/VuiBox";
import { ThemeToggleIconButton } from "components/VuiThemeToggle";

import { navLabel } from "@/lib/nav-label";

import { Menu as MenuGlyph } from "lucide-react";
import { useTranslations } from "next-intl";

// Vision UI Dashboard React example components
import Breadcrumbs from "examples/Breadcrumbs";
import NotificationsMenu from "examples/Navbars/DashboardNavbar/NotificationsMenu";

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
  setMobileNavOpen,
  setTransparentNavbar,
} from "context";

function DashboardNavbar({
  absolute = false,
  light = false,
  isMini = false,
  brand = false,
  actions = null,
}) {
  const [navbarType, setNavbarType] = useState();
  const t = useTranslations("nav");
  const [controller, dispatch] = useVisionUIController();
  const { transparentNavbar, fixedNavbar, mobileNavOpen } = controller;
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

  return (
    <AppBar
      position={absolute ? "absolute" : navbarType}
      color="inherit"
      sx={(theme) => navbar(theme, { transparentNavbar, absolute, light })}
    >
      <Toolbar sx={(theme) => navbarContainer(theme)}>
        <VuiBox
          color="inherit"
          mb={{ xs: 1, md: 0 }}
          sx={(theme) => ({
            ...navbarRow(theme, { isMini }),
            display: "flex",
            alignItems: "center",
            gap: theme.spacing(1),
            // `navbarRow` sets `space-between` below `md`, which with a second
            // child in this row threw the breadcrumb to the far right and left the
            // menu button on its own. They belong together at the left edge.
            justifyContent: "flex-start",
          })}
        >
          {/*
            The drawer's opener, on phones only.

            Below `md` the drawer is a temporary overlay that starts closed, so
            without this there is no way to reach the navigation at all. Above
            `md` the drawer is docked and always visible, and a button to open an
            already-open drawer would do nothing — hence the breakpoint rather
            than an always-on button.
          */}
          <IconButton
            size="small"
            color="inherit"
            aria-label={mobileNavOpen ? t("collapseSidebar") : t("expandSidebar")}
            title={mobileNavOpen ? t("collapseSidebar") : t("expandSidebar")}
            onClick={() => setMobileNavOpen(dispatch, !mobileNavOpen)}
            // Explicit media query, not the `{ xs: …, md: … }` shorthand: spread
            // alongside `navbarIconButton` the shorthand did not resolve, and this
            // button stayed visible on desktop where the drawer is already docked
            // and there is nothing for it to open.
            sx={(theme) => ({
              ...navbarIconButton,
              display: "none",
              [theme.breakpoints.down("md")]: { display: "inline-flex" },
            })}
          >
            <MenuGlyph size={20} />
          </IconButton>
          <Breadcrumbs
            icon="home"
            /*
              The page's name, not its URL slug. `route[0]` is the first path
              segment with the locale already stripped -- `usePathname` from
              `@/i18n/navigation` does that -- which is the same value the
              drawer matches its active entry on, so the two always name the
              page the same way. See `@/lib/nav-label`.

              The last segment rather than the first is what used to be passed.
              Every route in this app is one level deep, so they were the same
              value until a detail page appeared; the first segment is the one
              that names the *page*, which is what a title wants.
            */
            title={navLabel(t, route[0], route[route.length - 1] ?? "")}
            route={route}
            light={light}
            brand={brand}
          />
          {/* Page-scoped controls, beside the title. /chat puts its new-chat and
              history buttons here; every other page passes nothing. */}
          {actions}
        </VuiBox>
        {isMini ? null : (
          <VuiBox
            sx={(theme) => ({
              ...navbarRow(theme, { isMini }),
              // The whole row goes, not just its contents: `navbarRow` gives it
              // `width: 100%` below `md`, so hiding only the buttons would leave
              // an empty full-width line under the breadcrumb. Above `md`
              // nothing changes -- it is the same flex row it always was.
              // Written as an explicit media query rather than MUI's
              // `{ xs: "none", md: "flex" }` shorthand. The shorthand resolved to
              // `none` at every width here -- it does not survive being spread
              // alongside `navbarRow`'s own raw `[breakpoints.up("md")]` key -- so
              // the cluster vanished on desktop too. This is also how `navbarRow`
              // itself expresses its breakpoints, two lines up.
              display: "flex",
              [theme.breakpoints.down("md")]: { display: "none" },
            })}
          >
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
            {/*
              Phones do not get this row at all.

              Below `md` the navbar is two stacked lines, and four icon buttons
              wrapped onto their own line under the breadcrumb -- bolted to the
              top of a page they have nothing to do with. They live in the
              navigation overlay there instead, drawn as labelled rows by
              `SidenavActions`, which is the only place they are rendered on a
              phone: hidden here and shown there, never both.
            */}
            <VuiBox color={light ? "white" : "inherit"}>
              <NotificationsMenu
                renderTrigger={(triggerProps, unread) => (
                  <IconButton
                    {...triggerProps}
                    size="small"
                    color="inherit"
                    sx={navbarIconButton}
                    variant="contained"
                    aria-label={
                      unread ? t("notificationsUnread", { count: unread }) : t("notifications")
                    }
                    title={
                      unread ? t("notificationsUnread", { count: unread }) : t("notifications")
                    }
                  >
                    {/*
                      The count is on the bell rather than beside it: this row is
                      three icons wide and a number added to it would push the
                      cluster off the toolbar's right edge on a tablet.

                      `--primary`, not MUI's `error`. An unread report is
                      something waiting, not something wrong, and the template's
                      error red on a bell reads as a failure.
                    */}
                    <Badge
                      badgeContent={unread}
                      max={99}
                      overlap="circular"
                      sx={{
                        "& .MuiBadge-badge": {
                          backgroundColor: "var(--primary)",
                          color: "var(--on-brand)",
                          fontSize: "0.625rem",
                          minWidth: 16,
                          height: 16,
                        },
                      }}
                    >
                      <Icon
                        sx={({ palette: { dark, white } }) => ({
                          color: light ? white.main : dark.main,
                        })}
                      >
                        notifications
                      </Icon>
                    </Badge>
                  </IconButton>
                )}
              />
              {/* No drawer toggle here. The drawer collapses to a rail that is
                  always on screen and carries its own toggle in its header --
                  visible expanded, and revealed on hover or focus when
                  collapsed -- so a second control in the navbar was pointing at
                  something the user could already see and reach. Counted from
                  the right edge the row is now profile, theme, notifications.

                  The language toggle used to sit here, between theme and
                  notifications. The site ships in Turkish only as of
                  2026-08-25, so there is nothing to switch to; the component is
                  kept at `components/_unmounted/LocaleToggle.tsx` for the day
                  there is. */}
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
                <IconButton sx={navbarIconButton} size="small" aria-label={t("profile")}>
                  <Icon
                    sx={({ palette: { dark, white } }) => ({
                      color: light ? white.main : dark.main,
                    })}
                  >
                    account_circle
                  </Icon>
                </IconButton>
              </Link>
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
  /** Head the page with the brand wordmark instead of its route title. */
  brand: PropTypes.bool,
  /** Page-scoped controls rendered next to the title. */
  actions: PropTypes.node,
};

export default DashboardNavbar;
