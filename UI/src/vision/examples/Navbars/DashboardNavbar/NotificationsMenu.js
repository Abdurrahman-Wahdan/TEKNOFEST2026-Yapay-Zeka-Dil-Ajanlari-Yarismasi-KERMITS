"use client";

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
import MenuItem from "@mui/material/MenuItem";
import Icon from "@mui/material/Icon";

// Vision UI Dashboard React components
import VuiBox from "components/VuiBox";
import VuiTypography from "components/VuiTypography";

// Vision UI Dashboard React example components
import NotificationItem from "examples/Items/NotificationItem";

import { useRouter } from "@/i18n/navigation";
import { REPORTS_PATH, reportSearch } from "@/lib/automations";
import { formatDateTime } from "@/lib/format";
import {
  useUnreadReportCount,
  useUnreadReports,
} from "@/components/widgets/ReportNotifications";

/**
 * The notifications menu: the automation reports this user has not opened.
 *
 * The template shipped three hardcoded items — a message from Laur, an album by
 * Travis Scott, a completed payment. They are gone. What a notification means in
 * this app is one thing: an automation finished and wrote a report nobody has
 * read yet. There is no separate notification store, because a notification here
 * carries nothing the report does not — `read_at IS NULL` *is* the notification,
 * and opening the report clears it.
 *
 * Two surfaces open this: the navbar's icon button on a tablet and up, and the
 * drawer's row on a phone, where the whole action cluster moves into the
 * navigation overlay. The **trigger** stays with each surface — an icon among
 * icons and a row in a list are not the same control — but the menu and its
 * items live here once, so the phone does not get a second copy to drift from.
 *
 * `renderTrigger` is handed the props that open the menu **and the unread
 * count**, so each surface draws its own badge. The count has to reach the
 * trigger rather than the menu: a badge is only useful before the menu opens.
 */
function NotificationsMenu({ renderTrigger }) {
  const [openMenu, setOpenMenu] = useState(false);
  const router = useRouter();
  const unread = useUnreadReportCount();
  const { reports, isError, isLoading, locale, t } = useUnreadReports();

  const handleOpenMenu = (event) => setOpenMenu(event.currentTarget);
  const handleCloseMenu = () => setOpenMenu(false);

  const open = (reportId) => {
    handleCloseMenu();
    const search = reportSearch("", reportId);
    router.push(`${REPORTS_PATH}${search ? `?${search}` : ""}`);
  };

  return (
    <>
      {renderTrigger(
        {
          onClick: handleOpenMenu,
          "aria-controls": "notification-menu",
          "aria-haspopup": "true",
        },
        unread,
      )}
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
        {/*
          Loading is deliberately not a state here. The menu opens instantly and
          the poll has almost always resolved by the time it is clicked; a
          skeleton inside a popover that is one line tall reads as a glitch.
        */}
        {!isLoading && isError && (
          <Empty label={t("notificationsFailed")} />
        )}
        {!isLoading && !isError && reports.length === 0 && (
          <Empty label={t("notificationsEmpty")} />
        )}
        {reports.map((report) => (
          <NotificationItem
            key={report.id}
            color="text"
            image={
              <Icon
                fontSize="small"
                sx={{ color: ({ palette: { white } }) => white.main }}
              >
                description
              </Icon>
            }
            /*
              `title` is a two-part tuple the template renders as `<strong>{0}</strong> {1}`.
              The report's own title goes in the bold half; the second is left
              empty rather than padded with a word, because the title is already
              a sentence fragment the user wrote.
            */
            title={[report.title, ""]}
            date={formatDateTime(report.created_at, locale)}
            onClick={() => open(report.id)}
          />
        ))}
        {/*
          Always reachable, even with nothing unread: the menu is the only
          entry point to the Reports page that does not go through Profile, and
          "I read it yesterday, where is it" is a normal thing to want.
        */}
        <MenuItem onClick={() => open(null)}>
          <VuiTypography variant="button" fontWeight="regular" color="text">
            {t("notificationsAll")}
          </VuiTypography>
        </MenuItem>
      </Menu>
    </>
  );
}

function Empty({ label }) {
  return (
    <VuiBox px={2} py={1.5} sx={{ maxWidth: 260 }}>
      <VuiTypography variant="button" fontWeight="regular" color="text">
        {label}
      </VuiTypography>
    </VuiBox>
  );
}

Empty.propTypes = {
  label: PropTypes.string.isRequired,
};

// Typechecking props for the NotificationsMenu
NotificationsMenu.propTypes = {
  /**
   * Called with `(triggerProps, unreadCount)`; returns the element the props go
   * on. The count is passed so each surface can draw its own badge.
   */
  renderTrigger: PropTypes.func.isRequired,
};

export default NotificationsMenu;
