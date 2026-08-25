/**
 * Which heading a source belongs under in an answer's Kaynaklar panel.
 *
 * Three kinds, and the distinction is not cosmetic:
 *
 * - `online` — a bank's own page, found by live web research.
 * - `knowledge-base` — an indexed document out of the Qdrant corpus.
 * - `site` — a comparison table on *this* site, which the assistant offered as
 *   somewhere to go next. It is not evidence for anything: `find_comparison_table`
 *   returns no rate, fee or condition, so a table must never read as the support
 *   for a claim. `api/agent.py` reads these out of the finished prose rather than
 *   from the tool-evidence ledger, and marks them with `SITE_PAGE`.
 *
 * Split out of `ChatMessage` so the rules can be tested. The panel is the last
 * place a mislabelled source can be caught, and "which group" is exactly the
 * decision that tells a reader what a link is.
 */

/** `source_type` for one of our own pages, set by `api/agent.py`. */
export const SITE_PAGE = "site_page";

export type SourceGroup = "online" | "knowledge-base" | "site";

/** An openable http(s) URL, or null. */
export function safeWebSource(url: string): URL | null {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed : null;
  } catch {
    return null;
  }
}

/**
 * The group for one source, or null to drop it.
 *
 * Our pages are recognised by `sourceType`, never by "the url looks relative":
 * this decides what the reader is *told* a link is, and inferring that from a url
 * shape would silently mis-file any future relative link. The absolute-url parse
 * still gates everything else — a source the UI cannot turn into an openable
 * link is worse than a missing one.
 */
export function sourceGroup(
  sourceType: string | undefined,
  url: string,
): SourceGroup | null {
  if (sourceType === SITE_PAGE) return url.startsWith("/") ? "site" : null;
  if (!safeWebSource(url)) return null;
  return sourceType === "indexed_document" ? "knowledge-base" : "online";
}

/**
 * The route segment of one of our pages — `kampanyalar` for
 * `/tr/kampanyalar?tablo=...`.
 *
 * It doubles as the `nav` translation key, which is how a source card's second
 * line reads "Kampanyalar" in the same words the drawer and the page header use
 * (see `@/lib/nav-label`). Empty string when there is no segment to name, which
 * `navLabel` turns into its fallback rather than a blank line.
 */
export function siteSection(url: string): string {
  return url.split("?")[0].split("/").filter(Boolean)[1] ?? "";
}
