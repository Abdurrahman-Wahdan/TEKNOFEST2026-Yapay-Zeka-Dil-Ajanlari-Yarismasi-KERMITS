/**
 * The display name for a route, from its first path segment.
 *
 * One rule, two call sites: the drawer entry in `vision/examples/Sidenav` and
 * the page title in `vision/examples/Navbars/DashboardNavbar`. They used to
 * disagree, and visibly — the drawer read its label from the Turkish literal in
 * `vision/routes.js` while the title rendered the URL slug through CSS
 * `capitalize`. So `/urunler` was "Ürünler" in the drawer and "Urunler" in the
 * header, and `/ai-overview` was "AI Görünümü" beside "Ai Overview". A slug has
 * no Turkish characters in it and no casing worth showing; it is an identifier,
 * not a label.
 *
 * The `nav` namespace of `messages/tr.json` is the source, and its page keys are
 * **the route segment verbatim** — `ai-overview`, not `aiOverview` — so this is
 * a lookup rather than a slug-to-key map that could fall out of step with the
 * routes it names.
 *
 * `t.has` first, because a missing key makes next-intl throw `MISSING_MESSAGE`
 * and the drawer and header sit on every page in the app: a page added without
 * its label should render under a plain fallback, not take the whole shell down.
 */
export function navLabel(
  t: { (key: string): string; has: (key: string) => boolean },
  segment: string,
  fallback: string,
): string {
  return segment && t.has(segment) ? t(segment) : fallback;
}
