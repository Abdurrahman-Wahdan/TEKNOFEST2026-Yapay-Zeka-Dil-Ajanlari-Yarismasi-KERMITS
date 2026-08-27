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

// prop-types is a library for typechecking of props.
import PropTypes from "prop-types";

// @mui material components
import Collapse from "@mui/material/Collapse";
import useMediaQuery from "@mui/material/useMediaQuery";
import { useTheme } from "@mui/material/styles";
import ListItem from "@mui/material/ListItem";
import ListItemIcon from "@mui/material/ListItemIcon";
import Tooltip from "@mui/material/Tooltip";
import ListItemText from "@mui/material/ListItemText";
import Icon from "@mui/material/Icon";

// Vision UI Dashboard React components
import VuiBox from "components/VuiBox";

// Custom styles for the SidenavCollapse
import {
  collapseItem,
  collapseIconBox,
  collapseIcon,
  collapseText,
} from "examples/Sidenav/styles/sidenavCollapse";

// Vision UI Dashboard React context
import { useVisionUIController } from "context";

function SidenavCollapse({ color = "info", icon, name, children = false, active = false, noCollapse = false, open = false, ...rest }) {
  const [controller] = useVisionUIController();
  const { miniSidenav, transparentSidenav } = controller;

  /**
   * On a phone the drawer is a full-width overlay, so the labels belong on show
   * whatever the user's desktop rail preference was.
   *
   * `miniSidenav` is that preference and it is persisted, so a user who collapsed
   * the drawer on their laptop arrived on their phone to an overlay 250px wide
   * showing bare icons -- all the room for a label and no label in it.
   */
  const theme = useTheme();
  const isPhone = useMediaQuery(theme.breakpoints.down("md"));
  const railed = miniSidenav && !isPhone;

  return (
    <>
      <ListItem component="li">
        {/* Collapsed, the label is the only thing naming the destination and it
            is faded to nothing -- so the tooltip carries the name instead. Not
            rendered when expanded, where it would just repeat the visible
            label under the pointer. */}
        <Tooltip title={railed ? name : ""} placement="right" disableInteractive>
        <VuiBox
          {...rest}
          sx={(theme) => collapseItem(theme, { active, transparentSidenav, miniSidenav: railed })}
        >
          <ListItemIcon
            sx={(theme) => collapseIconBox(theme, { active, transparentSidenav, color })}
          >
            {typeof icon === "string" ? (
              <Icon sx={(theme) => collapseIcon(theme, { active })}>{icon}</Icon>
            ) : (
              icon
            )}
          </ListItemIcon>

          <ListItemText
            primary={name}
            sx={(theme) => collapseText(theme, { miniSidenav: railed, transparentSidenav, active })}
          />
        </VuiBox>
        </Tooltip>
      </ListItem>
      {children && (
        <Collapse in={open} unmountOnExit>
          {children}
        </Collapse>
      )}
    </>
  );
}

// Typechecking props for the SidenavCollapse
SidenavCollapse.propTypes = {
  color: PropTypes.oneOf(["info", "success", "warning", "error", "dark"]),
  icon: PropTypes.node.isRequired,
  name: PropTypes.string.isRequired,
  children: PropTypes.node,
  active: PropTypes.bool,
  noCollapse: PropTypes.bool,
  open: PropTypes.bool,
};

export default SidenavCollapse;
