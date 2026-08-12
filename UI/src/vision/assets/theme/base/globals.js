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

// Vision UI Dashboard React Base Styles
// Served from /public rather than imported: Next resolves a static image
// import to a StaticImageData object, and this template interpolates the
// value straight into CSS url(...) — which would emit [object Object].
const bgAdmin = "/vision/images/body-background.png";
// Takes `colors` so the theme can be rebuilt per light/dark mode; it used to
// read a module-scope object frozen at import time.
export default (colors) => {
  const { info, dark } = colors;
  return {
    html: {
      scrollBehavior: "smooth",
      background: dark.body,
    },
    body: {
      // Our palette background, not the template's purple artwork.
      //
      // `body-background.png` is a baked purple gradient, so it overrode the
      // palette on every page — no colour token could reach it, and it was the
      // main reason the dashboard did not look like our theme. The file is
      // still at the path in `bgAdmin` above if we want it back: restore
      // `background: url(${bgAdmin})` here.
      background: dark.body,
      backgroundSize: "cover",
    },
    "*, *::before, *::after": {
      margin: 0,
      padding: 0,
    },
    "a, a:link, a:visited": {
      textDecoration: "none !important",
    },
    "a.link, .link, a.link:link, .link:link, a.link:visited, .link:visited": {
      color: `${dark.main} !important`,
      transition: "color 150ms ease-in !important",
    },
    "a.link:hover, .link:hover, a.link:focus, .link:focus": {
      color: `${info.main} !important`,
    },
  };
};
