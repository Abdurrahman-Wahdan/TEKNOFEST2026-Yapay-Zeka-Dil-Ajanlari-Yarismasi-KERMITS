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
import sidenavLogoLabel from "examples/Sidenav/styles/sidenav";

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

  const closeSidenav = () => setMiniSidenav(dispatch, true);

  const handleSignOut = () => {
    logout();
    router.replace("/login");
  };

  useEffect(() => {
    // A function that sets the mini state of the sidenav.
    function handleMiniSidenav() {
      setMiniSidenav(dispatch, window.innerWidth < 1200);
    }

    /** 
     The event listener that's calling the handleMiniSidenav function when resizing the window.
    */
    window.addEventListener("resize", handleMiniSidenav);

    // Call the handleMiniSidenav function to set the state with the initial value.
    handleMiniSidenav();

    // Remove event listener on cleanup
    return () => window.removeEventListener("resize", handleMiniSidenav);
  }, [dispatch, location]);

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
    <SidenavRoot {...rest} variant="permanent" ownerState={{ transparentSidenav, miniSidenav }}>
      <VuiBox
        pt={3.5}
        pb={0.5}
        px={4}
        textAlign="center"
        sx={{
          overflow: "unset !important",
        }}
      >
        <VuiBox
          display={{ xs: "block", xl: "none" }}
          position="absolute"
          top={0}
          right={0}
          p={1.625}
          onClick={closeSidenav}
          sx={{ cursor: "pointer" }}
        >
          <VuiTypography variant="h6" color="text">
            <Icon sx={{ fontWeight: "bold" }}>close</Icon>
          </VuiTypography>
        </VuiBox>
        <VuiBox component={NavLink} to="/" display="flex" alignItems="center">
          <VuiBox
            sx={
              ((theme) => sidenavLogoLabel(theme, { miniSidenav }),
              {
                display: "flex",
                alignItems: "center",
                margin: "0 auto",
              })
            }
          >
            <VuiBox
              display="flex"
              sx={
                ((theme) => sidenavLogoLabel(theme, { miniSidenav, transparentSidenav }),
                {
                  mr: miniSidenav || (miniSidenav && transparentSidenav) ? 0 : 1,
                })
              }
            >
              {/* `height`/`width` as a plain HTML attribute only accepts a
                  bare number — "24px" is invalid and the browser silently
                  drops it, which is why this rendered at the image's full
                  natural size (301x225) instead of icon-sized. The size has
                  to go through `style` instead. */}
              <img
                src={kermitsLogo}
                alt=""
                style={{ display: "block", height: "40px", width: "auto" }}
              />
            </VuiBox>
            <VuiTypography
              variant="button"
              textGradient={true}
              color="logo"
              fontSize={14}
              letterSpacing={2}
              fontWeight="medium"
              sx={
                ((theme) => sidenavLogoLabel(theme, { miniSidenav, transparentSidenav }),
                {
                  opacity: miniSidenav || (miniSidenav && transparentSidenav) ? 0 : 1,
                  maxWidth: miniSidenav || (miniSidenav && transparentSidenav) ? 0 : "100%",
                  margin: "0 auto",
                })
              }
            >
              {brandName}
            </VuiTypography>
          </VuiBox>
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
            icon={<IoLogOut size="15px" color="inherit" />}
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
