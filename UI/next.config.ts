import path from "node:path";
import { fileURLToPath } from "node:url";

import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

const here = path.dirname(fileURLToPath(import.meta.url));

const nextConfig: NextConfig = {
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
