/**
 * Reading a saved dashboard view, and naming one.
 *
 * A `SavedView` holds a list of `{type, props}` written by the agent, and
 * `RenderComponent` is the only thing that validates it. That makes this module's
 * job narrow and specific: get the specs *out* of a row safely enough that the
 * page reaches `RenderComponent` at all. A malformed component must arrive as a
 * visible placeholder, not as a crash three layers above the code that could
 * explain it.
 */

import type { ComponentSpec } from "./contract.ts";
import type { SavedView } from "./api.ts";

/** The `slug` column's width, and the pattern the API validates against. */
export const SLUG_CHARS = 80;

/**
 * Turkish letters, transliterated to ASCII.
 *
 * The mirror of `_TR` in `api/saved_tables.py`. Both exist because a slug must
 * match `^[a-z0-9-]{1,80}$` while titles are Turkish: "Konut finansmanı" has to
 * become `konut-finansmani`, and simply dropping `ı` would merge distinct titles
 * onto one slug.
 */
const TR: Record<string, string> = {
  ç: "c", Ç: "c",
  ğ: "g", Ğ: "g",
  ı: "i", İ: "i",
  ö: "o", Ö: "o",
  ş: "s", Ş: "s",
  ü: "u", Ü: "u",
};

/**
 * A title as an identifier: lowercase ASCII letters, digits and hyphens.
 *
 * **Kept byte-for-byte in step with `slugify` in `api/saved_tables.py`.** The two
 * have to agree or the same table saves twice under two slugs — the agent writing
 * one and the "save this table" button writing another.
 *
 * Transliteration happens *before* lowercasing, and that order is the reason both
 * implementations are written this way rather than the obvious way: `"İ"`
 * lowercases to `i` + U+0307 in Python but to `i̇` in JavaScript, so lowering
 * first is exactly where the two would silently diverge.
 */
export function slugifyTitle(title: string, fallback = "tablo"): string {
  const transliterated = Array.from(title)
    .map((c) => TR[c] ?? c)
    .join("");
  return (
    transliterated
      .toLowerCase()
      .normalize("NFKD")
      // Strips the combining marks NFKD just split off, so an accented Latin
      // letter becomes its base rather than vanishing.
      .replace(/[̀-ͯ]/g, "")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, SLUG_CHARS) || fallback
  );
}

/**
 * The renderable components of a saved view.
 *
 * Defensive on two counts, both of which are real rather than theoretical. The
 * generated `Component.props` is optional, so `props` genuinely can be absent and
 * has to become `{}` — `RenderComponent` will then report a table with no rows,
 * which is a visible, specific complaint. And `components` is untyped JSONB that
 * the agent writes, so an entry that is not an object at all gets dropped here:
 * there is nothing `RenderComponent` could say about `null`, and letting it
 * through would throw before it got the chance to try.
 */
export function savedViewSpecs(view: Pick<SavedView, "components">): ComponentSpec[] {
  const raw = Array.isArray(view.components) ? view.components : [];
  const specs: ComponentSpec[] = [];
  for (const entry of raw) {
    if (typeof entry !== "object" || entry === null) continue;
    const { type, props } = entry as { type?: unknown; props?: unknown };
    if (typeof type !== "string" || type === "") continue;
    specs.push({
      type,
      props: typeof props === "object" && props !== null ? props : {},
    });
  }
  return specs;
}

/**
 * A saved view's heading, read defensively and **rendered raw**.
 *
 * The agent wrote this string. Passing it through `t()` would throw the moment it
 * is not a translation key, which is always.
 */
export function savedViewTitle(
  view: Pick<SavedView, "title">,
  fallback: string,
): string {
  return typeof view.title === "string" && view.title.trim() !== ""
    ? view.title
    : fallback;
}
