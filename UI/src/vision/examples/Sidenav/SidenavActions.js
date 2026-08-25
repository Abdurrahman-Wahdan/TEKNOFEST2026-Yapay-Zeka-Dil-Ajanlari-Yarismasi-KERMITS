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

import { useTransition } from "react";

// Next routing, via the typed navigation helpers
import { useParams } from "next/navigation";
import { usePathname, useRouter } from "@/i18n/navigation";
import { useLocale, useTranslations } from "next-intl";

// @mui material components
import List from "@mui/material/List";
import Divider from "@mui/material/Divider";

// Vision UI Dashboard React example components
import SidenavCollapse from "examples/Sidenav/SidenavCollapse";

// Vision UI Dashboard React icons
import { Languages } from "lucide-react";
import { ThemeGlyph } from "@/components/ui/ThemeGlyph";
import { useTheme } from "@/lib/theme";

/**
 * Each language's name in its own language, as in `LocaleToggleIconButton`
 * (now at `components/_unmounted/LocaleToggle.tsx` — the site is Turkish-only).
 *
 * Endonyms on purpose: a switcher offering "Turkish" to someone currently
 * reading English is only useful to people who already read English, which is
 * the wrong way round. This is also why the two names need no entry in the
 * message catalogs.
 */
const ENDONYM = { tr: "Türkçe", en: "English" };

/**
 * The navbar's action cluster, on a phone, as drawer rows.
 *
 * Above `md` these are four icon buttons flush with the navbar's right edge.
 * Below it the navbar stacks into two lines and they wrapped onto their own,
 * bolted under the breadcrumb — so `DashboardNavbar` hides that row at the same
 * breakpoint and they are drawn here instead, inside the navigation overlay
 * where the rest of the app's chrome already is. Rendered by `Sidenav` only when
 * `isPhone`, so there is exactly one copy on screen at any width.
 *
 * `SidenavCollapse` draws the rows, the same component the nav entries and Sign
 * Out use, so they inherit that row's padding, radius, hover and label
 * treatment rather than getting a fourth list style of their own. The glyphs are
 * the same ones the navbar buttons show — the lucide `Languages`, and
 * the shared `ThemeGlyph` — so the control does not change picture depending on
 * where you meet it.
 *
 * There is deliberately no Profile row: `routes.js` already has a Profile entry
 * in the nav list above, and a second one here would be two rows to the same
 * page in one drawer.
 *
 * The two toggles leave the drawer open. Both act instantly and in place — the
 * icon and label change under the finger — and dismissing the overlay would take
 * the control away from a user who wanted to see what it did, or who meant to
 * press it twice. A destination would be the other case, which is why the nav
 * entries close it and these do not.
 */
function SidenavActions() {
  const t = useTranslations("nav");
  const { theme, toggle } = useTheme();
  const locale = useLocale();
  const pathname = usePathname();
  const params = useParams();
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  // The glyph shows the mode a press would GIVE you, so the label names the same
  // thing. "Theme: Light" while the app is dark reads as the offer it is, and it
  // matches the language row right below it, which names the language a press
  // would switch to rather than the one you are reading.
  const nextTheme = theme === "dark" ? "light" : "dark";
  const themeLabel = `${t("theme")}: ${nextTheme === "dark" ? t("themeDark") : t("themeLight")}`;

  const nextLocale = locale === "tr" ? "en" : "tr";
  const localeLabel = `${t("language")}: ${ENDONYM[nextLocale]}`;

  // `router.replace(..., { locale })` rather than a link to `/en`: switching
  // language should keep you on the page you are reading, and should not stack a
  // history entry per toggle. `params` carries any dynamic segment of the current
  // route; without it a locale switch on a detail page 404s.
  const switchLocale = () => {
    if (pending) return;

    startTransition(() => {
      router.replace({ pathname, params }, { locale: nextLocale });
    });
  };

  return (
    <>
      <Divider light />
      <List>
        {/*
          Notifications is deliberately NOT here. On a phone it stays in the
          navbar as a bare icon on the right, where a notification indicator has
          to be reachable without opening the drawer first -- the whole point of
          a badge is that you see it in passing. Only the settings-like toggles
          moved in here.
        */}
        <SidenavCollapse
          name={localeLabel}
          icon={<Languages size={20} aria-hidden="true" />}
          onClick={switchLocale}
          noCollapse
        />
        <SidenavCollapse
          name={themeLabel}
          icon={<ThemeGlyph theme={theme} />}
          onClick={toggle}
          noCollapse
        />
      </List>
    </>
  );
}

export default SidenavActions;
