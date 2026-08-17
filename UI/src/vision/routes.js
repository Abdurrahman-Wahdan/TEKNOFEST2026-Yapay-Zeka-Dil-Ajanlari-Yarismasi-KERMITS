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

/** 
  All of the routes for the Vision UI Dashboard React are added here,
  You can add a new route, customize the routes and delete the routes here.

  Once you add a new route on this file it will be visible automatically on
  the Sidenav.

  For adding a new route you can follow the existing routes in the routes array.
  1. The `type` key with the `collapse` value is used for a route.
  2. The `type` key with the `title` value is used for a title inside the Sidenav. 
  3. The `type` key with the `divider` value is used for a divider between Sidenav items.
  4. The `name` key is used for the name of the route on the Sidenav.
  5. The `key` key is used for the key of the route (It will help you with the key prop inside a loop).
  6. The `icon` key is used for the icon of the route on the Sidenav, you have to add a node.
  7. The `collapse` key is used for making a collapsible item on the Sidenav that has other routes
  inside (nested routes), you need to pass the nested routes inside an array as a value for the `collapse` key.
  8. The `route` key is used to store the route location which is used for the react router.
  9. The `href` key is used to store the external links location.
  10. The `title` key is only for the item with the type of `title` and its used for the title text on the Sidenav.
  10. The `component` key from the original template is gone: Next resolves
  pages from the filesystem, so these entries only describe the Sidenav.
*/


// Vision UI Dashboard React icons
import { BsFillPersonFill } from "react-icons/bs";
import { BsCreditCardFill } from "react-icons/bs";
import { IoStatsChart } from "react-icons/io5";
import { IoHome } from "react-icons/io5";
// Filled variants, not the `*Outline` ones: every icon already in this Sidenav
// is a solid glyph (IoHome, IoStatsChart, BsCreditCardFill), and an outline
// icon sitting among them reads as a different weight rather than a different
// page.
import { IoDocumentText } from "react-icons/io5";
import { IoBusiness } from "react-icons/io5";
import { IoAlbums } from "react-icons/io5";
import { IoPricetags } from "react-icons/io5";
import { IoMegaphone } from "react-icons/io5";

const routes = [
  {
    type: "collapse",
    name: "Dashboard",
    key: "dashboard",
    route: "/dashboard",
    icon: <IoHome size="15px" color="inherit" />,
    noCollapse: true,
  },
  {
    type: "collapse",
    name: "Finansman",
    key: "finansman",
    route: "/finansman",
    icon: <IoDocumentText size="15px" color="inherit" />,
    noCollapse: true,
  },
  {
    type: "collapse",
    name: "Karşılaştır",
    key: "compare",
    route: "/compare",
    icon: <IoAlbums size="15px" color="inherit" />,
    noCollapse: true,
  },
  {
    type: "collapse",
    name: "Ürünler",
    key: "urunler",
    route: "/urunler",
    icon: <IoPricetags size="15px" color="inherit" />,
    noCollapse: true,
  },
  {
    type: "collapse",
    name: "Kampanyalar",
    key: "kampanyalar",
    route: "/kampanyalar",
    icon: <IoMegaphone size="15px" color="inherit" />,
    noCollapse: true,
  },
  {
    type: "collapse",
    name: "Bankalar",
    key: "banks",
    route: "/banks",
    icon: <IoBusiness size="15px" color="inherit" />,
    noCollapse: true,
  },
  {
    type: "collapse",
    name: "Tables",
    key: "tables",
    route: "/tables",
    icon: <IoStatsChart size="15px" color="inherit" />,
    noCollapse: true,
  },
  {
    type: "collapse",
    name: "Billing",
    key: "billing",
    route: "/billing",
    icon: <BsCreditCardFill size="15px" color="inherit" />,
    noCollapse: true,
  },
  // RTL is unmounted: no drawer entry and no route. The layout itself is kept
  // at `layouts/rtl/` — it is the template's worked example of a right-to-left
  // page, and the RTL machinery it demonstrates (theme-rtl, the stylis-plugin-rtl
  // emotion cache in VisionApp) is still live for when the app needs it.
  // No "Account Pages" heading: it grouped a section that has one item in it.
  // A heading over a single entry is noise, so Profile sits in the flat list
  // with everything else. Worth restoring only if the account section grows.
  {
    type: "collapse",
    name: "Profile",
    key: "profile",
    route: "/profile",
    icon: <BsFillPersonFill size="15px" color="inherit" />,
    noCollapse: true,
  },
  // Vision UI's own Sign In / Sign Up entries are gone: reaching the dashboard
  // already means signed in, and the app's real auth lives at /login and
  // /signup. The layouts stay at `layouts/authentication/*`.
];

export default routes;
