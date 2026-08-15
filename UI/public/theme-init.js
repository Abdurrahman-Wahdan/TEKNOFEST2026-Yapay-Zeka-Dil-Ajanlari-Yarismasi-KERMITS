/*
  Resolves the colour theme to a `.dark` class on <html> before first paint, so
  a dark-theme user never sees a white flash.

  A real file rather than an inline <script> in the layout: React never executes
  a script element it renders, so an inline tag is inert on client navigation —
  and React 19 now errors on one. `next/script` with `strategy="beforeInteractive"`
  loads this ahead of hydration.

  It falls back to the OS preference rather than leaving the class off, which
  keeps the class as the single signal — the palette in tailwind.css and
  Tailwind's `dark:` variant then agree by construction.
*/
(function () {
  try {
    var t = localStorage.getItem("tf26.theme");
    if (!t) {
      t = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }
    document.documentElement.classList.toggle("dark", t === "dark");
  } catch {
    /* private mode: the theme just falls back to the CSS media query */
  }
})();
