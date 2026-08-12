import { createNavigation } from "next-intl/navigation";

import { routing } from "./routing";

/**
 * Locale-aware replacements for next/link and next/navigation.
 *
 * Import `Link` from here, never from `next/link`: the plain one drops the
 * locale prefix, so an English user clicking a nav item silently lands back in
 * Turkish. Same for `useRouter` and `redirect`.
 */
export const { Link, redirect, usePathname, useRouter, getPathname } =
  createNavigation(routing);
