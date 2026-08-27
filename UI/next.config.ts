import path from "node:path";
import { fileURLToPath } from "node:url";

import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

const here = path.dirname(fileURLToPath(import.meta.url));

const nextConfig: NextConfig = {
  // Codex and local test clients open the dev server through 127.0.0.1 while
  // Next itself starts on localhost. Permit that same-machine origin so HMR can
  // load the current UI bundle rather than leaving a stale chat transport open.
  allowedDevOrigins: ["127.0.0.1"],

  sassOptions: {
    // Sass resolves its own imports and knows nothing about the `@/` alias in
    // tsconfig, so the tokens directory goes on the load path instead.
    loadPaths: [path.join(here, "src/styles")],
    // Every .module.scss gets the tokens and mixins without importing them.
    // Without this, 40 component stylesheets each open with the same two @use
    // lines, and the one that forgets them fails with "undefined variable"
    // rather than anything naming the cause.
    additionalData: `@use "tokens" as *;\n@use "mixins" as *;\n`,
  },

  async redirects() {
    /*
      `/en/...` is a dead locale as of 2026-08-25, and every link shared or
      bookmarked while English existed still points at one.

      Here rather than in `proxy.ts` because that is what Next recommends for a
      redirect that needs no request data, and because `proxy.ts` delegates
      wholesale to next-intl's middleware -- wrapping it to special-case one
      prefix would put app routing logic inside locale negotiation.

      Left to next-intl this 404s rather than redirecting: with `en` gone from
      `routing.locales` the middleware stops reading `/en` as a locale segment
      and treats it as the first path segment of an unprefixed URL, sending
      `/en/compare` to `/tr/en/compare`, which is not a route.

      `permanent: false` -- a 307, not a 308. A 308 is cached by the browser
      indefinitely, and these paths become real again the day a second language
      does.
    */
    return [
      { source: "/en", destination: "/tr", permanent: false },
      { source: "/en/:path*", destination: "/tr/:path*", permanent: false },
    ];
  },

  async rewrites() {
    // /api/* is proxied to FastAPI in development so the browser sees a single
    // origin. That removes a CORS preflight from every request, and leaves the
    // door open to a same-origin session cookie later without a second domain
    // to configure.
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.API_ORIGIN ?? "http://127.0.0.1:8000"}/api/:path*`,
      },
    ];
  },
};

export default withNextIntl(nextConfig);
