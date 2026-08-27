"use client";

import IconButton from "@mui/material/IconButton";
import type { SxProps, Theme } from "@mui/material/styles";
import { Languages } from "lucide-react";
import { useLocale, useTranslations } from "next-intl";
import { useParams } from "next/navigation";
import { useTransition } from "react";

import { usePathname, useRouter } from "@/i18n/navigation";

/**
 * Each language's name in its own language. Endonyms on purpose: a switcher
 * offering "Turkish" to someone currently reading English is only useful to
 * people who already read English, which is the wrong way round. This is also
 * why the two names need no entry in the message catalogs — they are the same
 * strings whichever locale is active.
 *
 * Keyed by `string`, not by the live `Locale` union: that union is `"tr"` alone
 * now, and a Record keyed by it would reject the `en` entry this file exists to
 * remember.
 */
type SwitchableLocale = "tr" | "en";
const ENDONYM: Record<SwitchableLocale, string> = { tr: "Türkçe", en: "English" };

/**
 * The navbar's language switch, sibling to `ThemeToggleIconButton`.
 *
 * There are exactly two locales, so this is a toggle rather than a menu: one
 * press, one round trip. It shows the lucide `Languages` glyph rather than the
 * target's name, because it sits in a row of icon-only controls and a lone text
 * button in that row reads as a different kind of control. The name a press
 * would give you is in the label and the tooltip instead — the same bargain
 * `ThemeGlyph` makes, where the picture is the affordance and the words carry
 * the meaning.
 *
 * `router.replace(..., { locale })` rather than a link to `/en`: switching
 * language should keep you on the page you are reading, and should not stack a
 * history entry per toggle.
 */
export function LocaleToggleIconButton({ sx }: { sx?: SxProps<Theme> }) {
  const t = useTranslations("nav");
  const active = useLocale() as SwitchableLocale;
  const pathname = usePathname();
  const params = useParams();
  const router = useRouter();
  const [pending, startTransition] = useTransition();

  const next: SwitchableLocale = active === "tr" ? "en" : "tr";
  // "Dil: English" / "Language: Türkçe" — the existing `nav.language` key names
  // the control, the endonym names what pressing it gives.
  const label = `${t("language")}: ${ENDONYM[next]}`;

  function switchTo(locale: SwitchableLocale) {
    startTransition(() => {
      // `params` carries any dynamic segment of the current route; without it a
      // locale switch on a detail page 404s.
      router.replace(
        // @ts-expect-error -- pathname and params are correlated at runtime,
        // which the typed-routes signature cannot express.
        { pathname, params },
        { locale },
      );
    });
  }

  return (
    <IconButton
      size="small"
      color="inherit"
      sx={sx}
      disabled={pending}
      onClick={() => switchTo(next)}
      aria-label={label}
      title={label}
    >
      <Languages size={20} aria-hidden="true" />
    </IconButton>
  );
}
