"""The address of one comparison table on this site, in both directions.

`/tr/kampanyalar?tablo=araç-bakım-ve-onarım-indirimi-kampanyası` is how a table
is addressed. Three places need to agree on that spelling and they are in two
languages, so the two Python directions live here together:

- `ui_url` builds it. `dataprep/stamp_table_urls.py` stamps the result onto every
  table's point in the `compare_tables` Qdrant collection as `ui_url`, which is
  what lets the assistant hand a reader a link to a table it is discussing.
- `parse_ui_url` reads it back. `api/agent.py` uses it to recognise a link the
  assistant wrote in its answer, so the UI can list that page under its own
  heading in the sources panel instead of dropping it.

The third place is `UI/src/lib/table-url.ts`, which cannot import any of this.
Its tests and `tests/unit/test_stamp_table_urls.py` pin the same strings on both
sides.

Site-relative on purpose, never absolute: the index outlives any hostname, and
`UI/src/components/chat/AgentMarkdown.tsx` keeps relative links in the app while
sending `http` ones to a new tab -- so a relative address takes the reader to the
table without discarding the conversation.
"""

from __future__ import annotations

import unicodedata
from urllib.parse import parse_qs, quote, urlsplit

# The query parameter carrying the table id, and the same name
# `UI/src/lib/table-url.ts` reads. Turkish, like the rest of this data domain
# (`Banka`, `Geçerlilik`, `Kaynak`).
PARAM = "tablo"

# Category -> App Router path segment, matching the folders at
# `UI/src/app/[locale]/(app)/{urunler,kampanyalar}/`. The keys are the pool's own
# two categories (`api/compare_tables_pool.CATEGORIES`).
ROUTE = {"ürün": "urunler", "kampanya": "kampanyalar"}
_CATEGORY = {route: category for category, route in ROUTE.items()}

# The site is single-locale (`UI/src/i18n/routing.ts`: locales = ["tr"]), but the
# prefix is still required: the App Router only has `/[locale]/...` pages, so an
# unprefixed `/kampanyalar` is not a route.
LOCALE = "tr"


def _nfc(value: str) -> str:
    """Turkish ids exist in two byte sequences -- NFC inside each JSON file, NFD
    on a macOS filesystem (see `compare_tables_pool.load_table`). Both directions
    normalise so an address does not depend on which form reached them."""
    return unicodedata.normalize("NFC", value or "")


def ui_url(table_id: str, category: str, base_url: str = "") -> str | None:
    """A table's address on this site. None when the category is unrecognised."""
    route = ROUTE.get(_nfc(category).strip().lower())
    if not route:
        return None
    return f"{base_url.rstrip('/')}/{LOCALE}/{route}?{PARAM}={quote(_nfc(table_id), safe='')}"


def parse_ui_url(url: str) -> tuple[str, str] | None:
    """`(table_id, category)` for one of our table addresses, else None.

    **The origin is ignored on purpose, including an absolute one.** What
    identifies one of our pages is the path, the section and the id -- never the
    host, which is not ours to trust or to preserve. Measured on 2026-08-25: the
    tool handed the model `/tr/urunler?tablo=altın-katılma-hesabı` and it wrote
    `https://www.kermits.com.tr/tr/urunler?tablo=...`, inventing a hostname that
    appears nowhere in this repository. Rejecting that dropped the page out of the
    sources panel entirely, which is the worst of the three outcomes: the reader
    got a link to a domain that may not exist and no card at all.

    So a decorated address is read for the table it names and the origin is
    discarded. Callers rebuild the canonical relative address with `ui_url`
    rather than echoing what they were given -- see `api.agent.site_table_sources`
    -- so a foreign host cannot survive into anything the reader clicks.

    Still strict about everything else: an unknown section, the wrong locale, a
    deeper path or a missing id is rejected rather than guessed at. The id is
    normalised to NFC and must still be resolved against the pool; a well-formed
    address for a table that does not exist is exactly what an invented slug
    looks like.
    """
    if not url:
        return None
    split = urlsplit(url)
    if split.scheme and split.scheme not in ("http", "https"):
        return None
    parts = split.path.strip("/").split("/")
    if len(parts) != 2 or parts[0] != LOCALE:
        return None
    category = _CATEGORY.get(parts[1])
    if not category:
        return None
    values = parse_qs(split.query).get(PARAM) or []
    table_id = _nfc(values[0]).strip() if values else ""
    return (table_id, category) if table_id else None
