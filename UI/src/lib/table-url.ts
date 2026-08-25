/**
 * Addressing one comparison table in the URL.
 *
 * `/tr/kampanyalar?tablo=araç-kiralama-indirim-kampanyası` opens that table
 * directly instead of the picker grid. Two things need this and they need to
 * agree exactly:
 *
 * - `CompareTablesBrowser`, which reads the parameter on load and writes it back
 *   when the reader opens or closes a table.
 * - `dataprep/stamp_table_urls.py`, which stamps the same address onto every
 *   table's point in the `compare_tables` Qdrant collection as `ui_url`, so the
 *   assistant can hand the reader a link to a table it is talking about.
 *
 * The parameter name is therefore duplicated across two languages — there is no
 * shared constant between Python and TypeScript. `PARAM` there, `TABLE_PARAM`
 * here, and a test on each side pins the spelling.
 *
 * Turkish, like the rest of this data domain (`Banka`, `Geçerlilik`, `Kaynak`).
 */
export const TABLE_PARAM = "tablo";

/**
 * The search string for a given selection — `"tablo=..."`, or `""` when nothing
 * is open. Other parameters that happen to be on the URL are preserved.
 *
 * `URLSearchParams` percent-encodes the id, which every id here needs: they are
 * Turkish slugs (`araç-kiralama-indirim-kampanyası`). Next decodes them again on
 * the way back in, so nothing has to unescape by hand.
 */
export function tableSearch(current: URLSearchParams | string, tableId: string | null): string {
  const params = new URLSearchParams(current);
  if (tableId) {
    params.set(TABLE_PARAM, tableId);
  } else {
    params.delete(TABLE_PARAM);
  }
  return params.toString();
}

/** The two sections a comparison table can live in — the App Router folders. */
const TABLE_ROUTES = ["urunler", "kampanyalar"];
const LOCALE = "tr";

/**
 * The in-app address for a link to one of our comparison tables, or null.
 *
 * The last stop before a click. The assistant is handed a site-relative address
 * and sometimes decorates it with a host it invented: measured on 2026-08-25 it
 * turned `/tr/urunler?tablo=altın-katılma-hesabı` into
 * `https://www.kermits.com.tr/tr/urunler?tablo=...`, a domain that appears
 * nowhere in this repository. Left alone that renders as an external link — a new
 * tab, a dead host, and the conversation abandoned.
 *
 * So the origin is discarded and the path is kept. Whatever host a link claims,
 * if it points at one of our table pages it is one of our table pages, and it
 * should open here. The backend applies the same rule to the sources panel
 * (`api/table_links.parse_ui_url`); this one is about the link in the prose.
 *
 * Only the two table routes, only `tr`, and only with an id — anything else is
 * somebody else's link and is returned as null so it stays external.
 */
export function internalTableHref(href: string | undefined): string | null {
  if (!href) return null;
  let path: string;
  let params: URLSearchParams;
  try {
    // A base makes one parser handle both forms. It is never part of the result:
    // only `pathname` and the query are read back out.
    const url = new URL(href, "https://tf26.invalid");
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    path = url.pathname;
    params = url.searchParams;
  } catch {
    return null;
  }
  const parts = path.split("/").filter(Boolean);
  if (parts.length !== 2 || parts[0] !== LOCALE || !TABLE_ROUTES.includes(parts[1])) return null;
  const id = params.get(TABLE_PARAM);
  if (!id) return null;
  return `/${LOCALE}/${parts[1]}?${tableSearch("", id)}`;
}
