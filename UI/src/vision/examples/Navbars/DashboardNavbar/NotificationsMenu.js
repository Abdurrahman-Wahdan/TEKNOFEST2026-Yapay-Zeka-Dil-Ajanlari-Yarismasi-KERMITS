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

import { useState } from "react";

// prop-types is a library for typechecking of props.
import PropTypes from "prop-types";

// @material-ui core components
import Menu from "@mui/material/Menu";
import Icon from "@mui/material/Icon";

// Vision UI Dashboard React example components
import NotificationItem from "examples/Items/NotificationItem";

// Images
// Served from /public rather than imported: Next resolves a static image
// import to a StaticImageData object, and this template interpolates the
// value straight into CSS url(...) — which would emit [object Object].
const team2 = "/vision/images/team-2.jpg";
// Served from /public rather than imported: Next resolves a static image
// import to a StaticImageData object, and this template interpolates the
// value straight into CSS url(...) — which would emit [object Object].
const logoSpotify = "/vision/images/small-logos/logo-spotify.svg";

/**
 * The notifications menu, and nothing but the menu.
 *
 * Two surfaces open it: the navbar's icon button on a tablet and up, and the
 * drawer's full-width labelled row on a phone, where the whole action cluster
 * moves into the navigation overlay. The *trigger* stays with each surface —
 * an icon in a row of icons and a labelled row in a list are not the same
 * control and should not pretend to be — but the menu and its items live here
 * once, so the phone does not get a second copy of the list to drift from.
 *
 * `renderTrigger` is handed the props that open the menu, so the caller keeps
 * ownership of the element and its styling. The anchor is whatever element
 * those props land on, which is why the menu opens beside the navbar button and
 * beside the drawer row without either surface knowing about the other.
 */
function NotificationsMenu({ renderTrigger }) {
  const [openMenu, setOpenMenu] = useState(false);

  const handleOpenMenu = (event) => setOpenMenu(event.currentTarget);
  const handleCloseMenu = () => setOpenMenu(false);

  return (
    <>
      {renderTrigger({
        onClick: handleOpenMenu,
        "aria-controls": "notification-menu",
        "aria-haspopup": "true",
      })}
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
    </>
  );
}

// Typechecking props for the NotificationsMenu
NotificationsMenu.propTypes = {
  // Called with the props that open the menu; returns the element they go on.
  renderTrigger: PropTypes.func.isRequired,
};

export default NotificationsMenu;
