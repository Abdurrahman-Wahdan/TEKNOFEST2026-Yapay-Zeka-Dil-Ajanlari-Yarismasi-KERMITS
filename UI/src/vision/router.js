"use client";

import { forwardRef, useMemo } from "react";

import { Link as IntlLink, usePathname } from "@/i18n/navigation";

/**
 * A react-router-dom stand-in for the Vision UI template.
 *
 * The template was built for react-router v5 and uses exactly two of its APIs:
 * `Link` (with a `to` prop) and `useLocation`. Rather than rewrite those call
 * sites — several pass `component={Link} to="..."` through MUI, where the prop
 * name matters — this maps them onto Next's router.
 *
 * Both come from `@/i18n/navigation`, not `next/link` and `next/navigation`,
 * so the locale is preserved: the plain versions would drop the `/en` prefix on
 * every sidebar click, and `usePathname` would return "/en/dashboard" and fail
 * to match the route table's "/dashboard", leaving no nav item highlighted.
 */
export const Link = forwardRef(function Link({ to, href, ...rest }, ref) {
  return <IntlLink ref={ref} href={to ?? href ?? "#"} {...rest} />;
});

/**
 * The template's Sidenav computes its own active state from `pathname`, so
 * NavLink needs nothing that Link does not already do.
 */
export const NavLink = Link;

/**
 * react-router's `useLocation`, narrowed to the fields the template reads.
 *
 * The `useMemo` is load-bearing, not tidiness. react-router v5 hands back the
 * same location object on every render until navigation, and Sidenav depends on
 * it directly: `useEffect(..., [dispatch, location])`. Returning a fresh object
 * each render makes that dependency change every time, so the effect dispatches,
 * which re-renders, which builds another new object — an infinite loop that
 * React reports as "Maximum update depth exceeded".
 */
export function useLocation() {
  const pathname = usePathname();
  return useMemo(
    () => ({ pathname, search: "", hash: "", state: null, key: pathname }),
    [pathname],
  );
}

export default { Link, NavLink, useLocation };
