/**
 * Tailwind v4 runs as a PostCSS plugin. It processes the plain-CSS entry
 * (src/styles/tailwind.css); the SCSS modules are compiled by Next's own Sass
 * pipeline and never reach Tailwind, which is what lets the two coexist.
 */
const config = {
  plugins: ["@tailwindcss/postcss"],
};

export default config;
