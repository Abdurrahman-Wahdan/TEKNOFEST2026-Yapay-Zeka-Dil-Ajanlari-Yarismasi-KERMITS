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

import { useEffect, useState } from "react";

// Next routing, via the react-router shim in vision/router.js
import { useLocation, NavLink } from "vision/router";
import { useRouter } from "@/i18n/navigation";
import { useAuth } from "@/lib/auth";

// prop-types is a library for typechecking of props.
import PropTypes from "prop-types";

// @mui material components
import List from "@mui/material/List";
import Divider from "@mui/material/Divider";
import Link from "@mui/material/Link";
import Icon from "@mui/material/Icon";

// Vision UI Dashboard React components
import VuiBox from "components/VuiBox";
import VuiTypography from "components/VuiTypography";

// Vision UI Dashboard React example components
import SidenavCollapse from "examples/Sidenav/SidenavCollapse";

// Custom styles for the Sidenav
import SidenavRoot from "examples/Sidenav/SidenavRoot";
import SidenavToggle from "examples/Sidenav/SidenavToggle";

// Vision UI Dashboard React context
import { useVisionUIController, setMiniSidenav, setTransparentSidenav } from "context";

// Vision UI Dashboard React icons
import { IoLogOut } from "react-icons/io5";
// Served from /public rather than imported: Next resolves a static image
// import to a StaticImageData object, and this template interpolates the
// value straight into an <img src>, which expects a plain string.
const kermitsLogo = "/vision/images/kermits-logo.png";

// function Sidenav({ color = "info", brand, brandName, routes, ...rest }) {
function Sidenav({ color, brandName, routes, ...rest }) {
  const [controller, dispatch] = useVisionUIController();
  const { miniSidenav, transparentSidenav } = controller;
  const location = useLocation();
  const { pathname } = location;
  const collapseName = pathname.split("/").slice(1)[0];
  const { logout } = useAuth();
  const router = useRouter();

  const toggleSidenav = () => setMiniSidenav(dispatch, !miniSidenav);

  // Whether the pointer is anywhere over the drawer. Collapsed, this is what
  // swaps the logo for the expand button -- checked against ChatGPT, where the
  // swap zone is the whole rail and not just the mark itself, so reaching for
  // the button from a nav icon lower down already reveals it.
  //
  // It does NOT expand the drawer. `VisionApp` used to hold hover handlers that
  // did exactly that; they are gone, because a rail that expands on hover moves
  // the page out from under the pointer.
  const [hovered, setHovered] = useState(false);

  const handleSignOut = () => {
    logout();
    router.replace("/login");
  };

  // There is deliberately no effect deriving `miniSidenav` from the window
  // width. One used to live here, running on mount, on every resize and -- via
  // a `location` dependency -- on every route change, which meant a collapse
  // the user performed was undone the moment they clicked a nav item. The state
  // is the user's now: seeded from the `tf26.sidenav` cookie in the app layout
  // and changed only by the two toggles.

  useEffect(() => {
    // The glass/transparent sidenav is a large-screen-only look: below xl the
    // sidenav becomes an overlay drawer on top of the page content, and a
    // transparent background there means the content behind bleeds straight
    // through it. This ran once on mount before, so shrinking the window
    // *after* load left it transparent — the drawer needs to react to every
    // resize, the same way handleMiniSidenav above already does.
    function handleTransparentSidenav() {
      if (window.innerWidth < 1440) {
        setTransparentSidenav(dispatch, false);
      }
    }

    window.addEventListener("resize", handleTransparentSidenav);
    handleTransparentSidenav();

    return () => window.removeEventListener("resize", handleTransparentSidenav);
  }, [dispatch]);

  // Render all the routes from the routes.js (All the visible items on the Sidenav)
  const renderRoutes = routes.map(({ type, name, icon, title, noCollapse, key, route, href }) => {
    let returnValue;

    if (type === "collapse") {
      returnValue = href ? (
        <Link
          href={href}
          key={key}
          target="_blank"
          rel="noreferrer"
          sx={{ textDecoration: "none" }}
        >
          <SidenavCollapse
            color={color}
            name={name}
            icon={icon}
            active={key === collapseName}
            noCollapse={noCollapse}
          />
        </Link>
      ) : (
        <NavLink to={route} key={key}>
          <SidenavCollapse
            color={color}
            key={key}
            name={name}
            icon={icon}
            active={key === collapseName}
            noCollapse={noCollapse}
          />
        </NavLink>
      );
    } else if (type === "title") {
      returnValue = (
        <VuiTypography
          key={key}
          color="white"
          display="block"
          variant="caption"
          fontWeight="bold"
          textTransform="uppercase"
          pl={3}
          mt={2}
          mb={1}
          ml={1}
        >
          {title}
        </VuiTypography>
      );
    } else if (type === "divider") {
      returnValue = <Divider light key={key} />;
    }

    return returnValue;
  });

  return (
    <SidenavRoot
      {...rest}
      variant="permanent"
      ownerState={{ transparentSidenav, miniSidenav }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {/*
        The header, and the two states are genuinely different content rather
        than one layout that shrinks.

        Expanded: the KERMİTS wordmark on the left, the collapse button on the
        right. No logo — the wordmark already is the brand here, and the mark
        beside it was saying the same thing twice in a 250px strip.

        Collapsed: the mark alone on the rail's centre line, becoming the expand
        button while the pointer is anywhere over the drawer.
      */}
      <VuiBox pt={3.5} pb={0.5} px={miniSidenav ? 1 : 3}>
        <VuiBox
          display="flex"
          alignItems="center"
          // Centred on the rail, spread apart when expanded. `space-between` on
          // a single centred child would pin it left, which is what put the
          // mark off-centre on the rail.
          justifyContent={miniSidenav ? "center" : "space-between"}
          sx={{ minHeight: 40 }}
        >
          {miniSidenav ? (
            /* A fixed 40px square holding both the mark and the button, stacked.
               Both are always rendered and cross-faded rather than swapped by a
               conditional: a button that only exists while the pointer is over
               the drawer cannot be reached from the keyboard at all, and this is
               the rail's only expand control. `focus-within` reveals it for the
               same reason hover does, so tabbing to it makes it visible. */
            <VuiBox
              sx={{
                position: "relative",
                height: 40,
                width: 40,
                "&:focus-within .sidenavToggleSlot": { opacity: 1 },
              }}
            >
              <VuiBox
                component={NavLink}
                to="/"
                display="flex"
                alignItems="center"
                justifyContent="center"
                sx={{
                  position: "absolute",
                  inset: 0,
                  lineHeight: 0,
                  opacity: hovered ? 0 : 1,
                  transition: (theme) =>
                    theme.transitions.create("opacity", {
                      duration: theme.transitions.duration.shortest,
                    }),
                }}
              >
                {/* `height`/`width` as plain HTML attributes only accept bare
                    numbers — "40px" is silently dropped, which is why this once
                    rendered at its natural 301x225. Size goes through `style`,
                    and `objectFit` keeps the mark centred in the square rather
                    than stretched to fill it. */}
                <img
                  src={kermitsLogo}
                  alt=""
                  style={{
                    display: "block",
                    height: "40px",
                    width: "40px",
                    objectFit: "contain",
                  }}
                />
              </VuiBox>
              <VuiBox
                className="sidenavToggleSlot"
                display="flex"
                alignItems="center"
                justifyContent="center"
                sx={{
                  position: "absolute",
                  inset: 0,
                  opacity: hovered ? 1 : 0,
                  transition: (theme) =>
                    theme.transitions.create("opacity", {
                      duration: theme.transitions.duration.shortest,
                    }),
                }}
              >
                <SidenavToggle miniSidenav={miniSidenav} onClick={toggleSidenav} />
              </VuiBox>
            </VuiBox>
          ) : (
            <>
              <VuiTypography
                variant="button"
                textGradient={true}
                color="logo"
                fontSize={14}
                letterSpacing={2}
                fontWeight="medium"
                sx={{ whiteSpace: "nowrap" }}
              >
                {brandName}
              </VuiTypography>
              <SidenavToggle miniSidenav={miniSidenav} onClick={toggleSidenav} />
            </>
          )}
        </VuiBox>
      </VuiBox>
      <Divider light />
      <List>{renderRoutes}</List>
      <VuiBox
        my={2}
        mx={2}
        mt="auto"
        sx={({ breakpoints }) => ({
          [breakpoints.up("xl")]: {
            pt: 2,
          },
          [breakpoints.only("xl")]: {
            pt: 1,
          },
          [breakpoints.down("xl")]: {
            pt: 2,
          },
        })}
      >
        {/*
          The "Need help? / DOCUMENTATION" card is deliberately not rendered —
          it links to Creative Tim's docs for the template, not this app.
          `SidenavCard` is untouched at `examples/Sidenav/SidenavCard`, so
          bringing it back is just restoring `<SidenavCard color={color} />`
          here. Sign Out takes its spot instead: the bottom of the sidenav,
          not tucked under Account Pages next to a Profile it isn't a peer of.

          The "Upgrade to PRO" button is deliberately not rendered either — it
          links to Creative Tim's store, which has nothing to do with this app.
          `VuiButton` is untouched; restoring it means putting back:

            <VuiBox mt={2}>
              <VuiButton
                component="a"
                href="https://creative-tim.com/product/vision-ui-dashboard-pro-react"
                target="_blank"
                rel="noreferrer"
                variant="gradient"
                color={color}
                fullWidth
              >
                Upgrade to PRO
              </VuiButton>
            </VuiBox>
        */}
        <List>
          <SidenavCollapse
            // `collapseIconBox`'s own styles force-paint every icon's `fill`
            // from this `color` prop ("& svg, svg g": { fill: ... }) — it
            // wins over any `fill`/`color` set directly on the icon element,
            // which is why the glyph ignored an inline colour and stayed the
            // ambient accent. "error" is the one already reserved for this —
            // routes.js icons all pass `color="inherit"` and let this same
            // mechanism recolour them, so Sign Out follows the same
            // convention rather than fighting it.
            color="error"
            name="Sign Out"
            icon={<IoLogOut size="20px" color="inherit" />}
            onClick={handleSignOut}
            noCollapse
          />
        </List>
      </VuiBox>
    </SidenavRoot>
  );
}

// Typechecking props for the Sidenav
Sidenav.propTypes = {
  color: PropTypes.oneOf(["primary", "secondary", "info", "success", "warning", "error", "dark"]),
  // brand: PropTypes.string,
  brandName: PropTypes.string.isRequired,
  routes: PropTypes.arrayOf(PropTypes.object).isRequired,
};

export default Sidenav;
