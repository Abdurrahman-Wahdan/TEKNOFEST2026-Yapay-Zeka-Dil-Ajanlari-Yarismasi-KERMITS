import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";

const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
    // Third-party source: the Vision UI template, kept as supplied so it can be
    // diffed against upstream. Its own style issues (forwardRef components with
    // no displayName, a setState inside useMemo in App.js) are Creative Tim's,
    // and "fixing" them would mean editing files we deliberately keep faithful.
    // Our own code is still linted — keep it that way.
    "src/vision/**",
  ]),

  {
    // `no-page-custom-font` is a Pages Router rule: it wants font <link>s in
    // `pages/_document.js`, which does not exist in an App Router project. The
    // root layout's <head> is exactly where they belong here, so the rule can
    // only ever be wrong in this file.
    //
    // Scoped to the one file rather than switched off globally, so a stray font
    // link anywhere else is still reported.
    files: ["src/app/**/layout.tsx"],
    rules: { "@next/next/no-page-custom-font": "off" },
  },
]);

export default eslintConfig;
