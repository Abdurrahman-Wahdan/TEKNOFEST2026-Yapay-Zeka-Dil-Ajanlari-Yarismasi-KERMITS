import createMiddleware from "next-intl/middleware";

import { routing } from "./i18n/routing";

/**
 * Locale negotiation, at the network boundary.
 *
 * `proxy.ts`, not `middleware.ts`: Next.js 16 deprecated the middleware
 * convention and renamed it, and the named export must be `proxy` to match.
 * The runtime is Node, which is what next-intl wants anyway.
 */
export const proxy = createMiddleware(routing);

export const config = {
  /**
   * Everything except Next's internals, the API proxy and static files.
   *
   * `api` must stay excluded: the rewrite to FastAPI would otherwise be treated
   * as a page and redirected to `/tr/api/...`, which is not an endpoint.
   */
  matcher: ["/((?!api|_next|_vercel|.*\\..*).*)"],
};
