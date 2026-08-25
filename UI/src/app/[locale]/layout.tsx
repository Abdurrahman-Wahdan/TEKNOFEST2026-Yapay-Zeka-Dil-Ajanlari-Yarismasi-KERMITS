import type { Metadata } from "next";
import { cookies } from "next/headers";
import { Geist, Open_Sans } from "next/font/google";
import { NextIntlClientProvider, hasLocale } from "next-intl";
import { setRequestLocale } from "next-intl/server";
import { notFound } from "next/navigation";

import { AuthProvider } from "@/lib/auth";
import { QueryProvider } from "@/lib/query";
import { routing } from "@/i18n/routing";
// Tailwind first, so the app's own reset and element defaults in globals.scss
// win where the two overlap.
import "@/styles/tailwind.css";
import "@/styles/globals.scss";

// Open Sans, as the palette specifies.
const openSans = Open_Sans({
  // Latin Extended carries ğ, ş, ı, İ, ç, ö, ü. Without it every Turkish
  // string falls back to a system font mid-sentence.
  subsets: ["latin", "latin-ext"],
  variable: "--font-open-sans",
  display: "swap",
});

// The sign-in screen asks for `font-geist`. Loaded here so that class resolves
// to the real typeface rather than silently falling through to the sans stack.
const geist = Geist({
  subsets: ["latin", "latin-ext"],
  // `--font-geist-sans`, not `--font-geist`: the latter is the name Tailwind's
  // @theme uses to define the `font-geist` utility itself, and defining it here
  // too would have the typeface reference itself.
  variable: "--font-geist-sans",
  display: "swap",
});

export const metadata: Metadata = {
  title: "TF26",
  description: "Turkish participation-banking campaigns and live pricing.",
};

/**
 * Turkish is the only locale, so this prerenders the single `/tr` segment. It
 * still reads from `routing.locales` rather than hardcoding `"tr"` — the list
 * is the one place a locale is declared, and a hardcoded copy here is what
 * would go stale if a second language ever came back.
 */
export function generateStaticParams() {
  return routing.locales.map((locale) => ({ locale }));
}

export default async function LocaleLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  // A Promise since Next.js 15: route params are async.
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  if (!hasLocale(routing.locales, locale)) {
    notFound();
  }

  // Without this, anything under here that reads a translation opts the whole
  // route out of static rendering.
  setRequestLocale(locale);

  /*
    The theme is resolved here, on the server, from the cookie the toggle
    writes — so the `.dark` class ships in the HTML and there is no flash.

    The alternative, an inline script that reads localStorage before paint, is
    what this replaces: React never executes a script element it renders, so
    React 19 errors on one whether it is inline or `next/script`.

    Dark is the default for a visitor with no cookie, because this is a dark
    dashboard — the light theme is the deliberate choice, so it is the one that
    needs a stored preference.
  */
  const theme = (await cookies()).get("tf26.theme")?.value === "light" ? "light" : "dark";

  return (
    <html
      lang={locale}
      className={`${openSans.variable} ${geist.variable}${theme === "dark" ? " dark" : ""}`}
      data-scroll-behavior="smooth"
      suppressHydrationWarning
    >
      <head>
        {/*
          Roboto and the Material Icons font, which the Vision UI template
          expects. CRA loaded these from its public/index.html; that file has no
          equivalent here, and without them MUI's <Icon> renders its ligature
          name as literal text ("settings", "menu") instead of a glyph.
        */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css?family=Roboto:300,400,500,700&display=swap"
        />
        {/* `block` rather than the rule's suggested `optional`, because this is
            an icon font. With `swap` the ligature name shows as literal text
            ("settings", "menu") until the font lands — the exact failure the
            note above describes — and with `optional` a slow connection can
            drop the font for the whole page load, leaving no icons at all.
            `block` costs a brief invisible period and then draws the glyph. */}
        {/* eslint-disable-next-line @next/next/google-font-display */}
        <link
          rel="stylesheet"
          href="https://fonts.googleapis.com/css?family=Material+Icons|Material+Icons+Outlined|Material+Icons+Two+Tone|Material+Icons+Round|Material+Icons+Sharp&display=block"
        />
      </head>
      <body>
        <NextIntlClientProvider>
          <QueryProvider>
            <AuthProvider>{children}</AuthProvider>
          </QueryProvider>
        </NextIntlClientProvider>
      </body>
    </html>
  );
}
