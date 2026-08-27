"""The UI address of a comparison table, in both directions.

This is a cross-language contract and nothing enforces it at build time: the
same parameter name and the same percent-encoding are written independently in
`UI/src/lib/table-url.ts`, whose own tests assert the identical strings. A
mismatch does not raise anywhere — it produces a link that opens the picker grid
instead of the table the assistant was talking about.
"""

import unicodedata

from api import table_links as links


def test_kampanya_and_urun_go_to_their_own_routes():
    """The two route folders under `UI/src/app/[locale]/(app)/`, and the `/tr`
    prefix every one of them sits behind."""
    assert links.ui_url("kredi-kartı", "ürün") == "/tr/urunler?tablo=kredi-kart%C4%B1"
    assert links.ui_url("bayram-kampanyası", "kampanya") == (
        "/tr/kampanyalar?tablo=bayram-kampanyas%C4%B1")


def test_the_parameter_is_the_one_the_ui_reads():
    assert links.PARAM == "tablo"


def test_turkish_ids_are_percent_encoded():
    """Every id in the pool is a Turkish slug. `URLSearchParams` on the other
    side produces exactly this, which is what makes the round trip work."""
    assert links.ui_url("araç-kiralama-indirim-kampanyası", "kampanya") == (
        "/tr/kampanyalar?tablo=ara%C3%A7-kiralama-indirim-kampanyas%C4%B1")


def test_the_address_does_not_depend_on_which_unicode_form_the_id_arrived_in():
    """macOS writes filenames in NFD while the id inside each file is NFC (see
    `compare_tables_pool.load_table`), so an id read off the disk and the same id
    read out of the JSON are different byte strings. Both have to stamp the same
    url, or half the links point at nothing."""
    nfc = unicodedata.normalize("NFC", "kredi-kartı-doğum-günü-kampanyaları")
    nfd = unicodedata.normalize("NFD", nfc)
    assert nfc != nfd
    assert links.ui_url(nfd, "kampanya") == links.ui_url(nfc, "kampanya")


def test_an_unknown_category_stamps_nothing_rather_than_guessing():
    """There is no third category today, and a wrong route is worse than no
    link: it would open the other half of the pool and look like a bug in the
    table rather than in the stamp. `main` counts these and reports them."""
    assert links.ui_url("t", "sigorta") is None
    assert links.ui_url("t", "") is None


def test_base_url_is_opt_in_and_leaves_no_double_slash():
    assert links.ui_url("t", "ürün") == "/tr/urunler?tablo=t"
    assert links.ui_url("t", "ürün", "https://app.example.com/") == (
        "https://app.example.com/tr/urunler?tablo=t")


# --- reading an address back ---------------------------------------------------
def test_an_address_round_trips_through_both_directions():
    """`api/agent.py` recognises a link the model wrote by parsing what
    `dataprep/stamp_table_urls.py` stamped. If these two ever disagree the
    assistant's own links stop being recognised as our pages."""
    for table_id, category in (
        ("araç-bakım-ve-onarım-indirimi-kampanyası", "kampanya"),
        ("kredi-kartı", "ürün"),
        ("2b-arazi-finansmanı", "ürün"),
    ):
        url = links.ui_url(table_id, category)
        assert links.parse_ui_url(url) == (table_id, category)


def test_an_invented_host_is_read_through_rather_than_rejected():
    """Measured on 2026-08-25: handed `/tr/urunler?tablo=altın-katılma-hesabı`,
    the model wrote `https://www.kermits.com.tr/tr/...` -- a hostname that appears
    nowhere in this repository. Rejecting it dropped the page out of the sources
    panel, leaving the reader a link to a domain that may not exist and no card.

    The origin is not ours to trust or to keep, so it is discarded and the table
    is read from the path. Callers rebuild the address with `ui_url`, so the
    foreign host never reaches anything the reader clicks."""
    assert links.parse_ui_url(
        "https://www.kermits.com.tr/tr/urunler?tablo=alt%C4%B1n-kat%C4%B1lma-hesab%C4%B1"
    ) == ("altın-katılma-hesabı", "ürün")
    assert links.parse_ui_url("http://anything.example/tr/kampanyalar?tablo=x") == ("x", "kampanya")


def test_parsing_rejects_anything_that_is_not_one_of_our_table_pages():
    """Whatever this lets through is shown to the reader as a page on this site,
    so a near-miss must fail rather than be guessed at."""
    assert links.parse_ui_url("javascript:/tr/urunler?tablo=x") is None         # not a web scheme
    assert links.parse_ui_url("/tr/kampanyalar") is None                        # no id
    assert links.parse_ui_url("/tr/kampanyalar?tablo=") is None                 # empty id
    assert links.parse_ui_url("/tr/profile?tablo=x") is None                    # not a table page
    assert links.parse_ui_url("/en/urunler?tablo=x") is None                    # wrong locale
    assert links.parse_ui_url("/urunler?tablo=x") is None                       # no locale prefix
    assert links.parse_ui_url("/tr/urunler/extra?tablo=x") is None              # deeper path
    assert links.parse_ui_url("") is None


def test_parsing_normalises_the_id_so_the_pool_lookup_matches():
    nfd = unicodedata.normalize("NFD", "kredi-kartı-doğum-günü")
    parsed = links.parse_ui_url(links.ui_url(nfd, "kampanya"))
    assert parsed is not None
    assert parsed[0] == unicodedata.normalize("NFC", "kredi-kartı-doğum-günü")


def test_a_well_formed_address_for_a_table_that_does_not_exist_still_parses():
    """Parsing is spelling only. Whether the table exists is the caller's job --
    `api/agent.py` resolves it against the pool, which is what catches an id the
    model invented."""
    assert links.parse_ui_url("/tr/urunler?tablo=boyle-bir-tablo-yok") == (
        "boyle-bir-tablo-yok", "ürün")
