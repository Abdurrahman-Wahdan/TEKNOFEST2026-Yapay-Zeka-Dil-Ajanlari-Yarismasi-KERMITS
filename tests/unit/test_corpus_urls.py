"""URL identity: the rules that decide what counts as one document.

Two of the crawler defects being replaced were identity bugs, so these are the
tests that keep them dead.
"""

import pytest

from banks import get_bank
from corpus.models import DOC_KINDS
from corpus.sites import SITES, get_site, list_sites
from corpus.urls import (
    canonicalise,
    doc_id,
    is_pdf,
    is_tracking_param,
    same_site,
    text_hash,
)

pytestmark = pytest.mark.unit


# ----- canonical form -----

def test_http_and_https_and_port_variants_are_one_document():
    """The corpus had 150 copies of one page under these three spellings."""
    plain = canonicalise("https://www.turkiyefinans.com.tr/tr-tr/hakkimizda")
    assert canonicalise("http://www.turkiyefinans.com.tr/tr-tr/hakkimizda") == plain
    assert canonicalise("https://www.turkiyefinans.com.tr:443/tr-tr/hakkimizda") == plain


def test_two_urls_differing_only_by_query_are_two_documents():
    """44 FAQ pages collapsed onto one file because the query was dropped."""
    konut = canonicalise("https://x.com.tr/sss.aspx?category=konutfinansmani")
    kredi = canonicalise("https://x.com.tr/sss.aspx?category=ihtiyacfinansmani")
    assert konut != kredi
    assert doc_id(konut) != doc_id(kredi)


def test_query_order_does_not_change_identity():
    """Two links to the same page must not become two documents."""
    assert canonicalise("https://x.com.tr/p?b=2&a=1") == canonicalise(
        "https://x.com.tr/p?a=1&b=2"
    )


def test_tracking_parameters_do_not_create_a_second_document():
    """Otherwise one campaign files under four ids depending on who linked it."""
    bare = canonicalise("https://x.com.tr/kampanya")
    assert canonicalise("https://x.com.tr/kampanya?utm_source=mail") == bare
    assert canonicalise("https://x.com.tr/kampanya?gclid=abc") == bare
    assert canonicalise("https://x.com.tr/kampanya?fbclid=xyz&utm_medium=cpc") == bare


def test_a_real_parameter_survives_alongside_a_tracking_one():
    assert canonicalise("https://x.com.tr/sss?category=konut&utm_source=mail") == (
        "https://x.com.tr/sss?category=konut"
    )


def test_the_fragment_is_dropped():
    assert canonicalise("https://x.com.tr/p#bolum-2") == "https://x.com.tr/p"


def test_a_trailing_slash_is_not_a_different_page():
    assert canonicalise("https://x.com.tr/a/b/") == canonicalise("https://x.com.tr/a/b")


def test_the_homepage_keeps_its_root_path():
    """Stripping every trailing slash would leave the homepage with no path."""
    assert canonicalise("https://x.com.tr/") == "https://x.com.tr/"
    assert canonicalise("https://x.com.tr") == "https://x.com.tr/"


def test_a_default_port_is_dropped_whichever_scheme_named_it():
    """"http://host:443/p" is one of the spellings that produced 150 duplicates."""
    plain = "https://x.com.tr/p"
    assert canonicalise("http://x.com.tr:443/p") == plain
    assert canonicalise("https://x.com.tr:443/p") == plain
    assert canonicalise("http://x.com.tr:80/p") == plain


def test_a_non_default_port_is_kept():
    assert canonicalise("https://x.com.tr:8443/p") == "https://x.com.tr:8443/p"


def test_the_host_is_lowercased():
    assert canonicalise("https://WWW.Kuveytturk.COM.TR/p") == (
        "https://www.kuveytturk.com.tr/p"
    )


def test_percent_encoded_and_literal_turkish_paths_are_one_document():
    """Bank links arrive both ways for the same contract PDF."""
    assert canonicalise("https://x.com.tr/Finans%C3%B6r") == canonicalise(
        "https://x.com.tr/Finansör"
    )


def test_a_site_can_force_its_own_canonical_host():
    """Nine banks use www. and Dünya does not, so folding is per-site."""
    assert canonicalise("https://kuveytturk.com.tr/p", host="www.kuveytturk.com.tr") == (
        "https://www.kuveytturk.com.tr/p"
    )


def test_a_non_http_url_canonicalises_to_nothing():
    """mailto: and javascript: links are all over these pages."""
    assert canonicalise("mailto:info@x.com.tr") == ""
    assert canonicalise("javascript:;") == ""
    assert canonicalise("") == ""


# ----- ids and hashes -----

def test_the_site_root_and_the_index_never_collide():
    """The homepage and the table of contents were one file on a case-insensitive
    filesystem, and seven banks lost their homepage to it. Ids are hashes now, so
    the two cannot share a name whatever the filesystem thinks."""
    root = doc_id(canonicalise("https://www.kuveytturk.com.tr/"))
    index = doc_id(canonicalise("https://www.kuveytturk.com.tr/index"))
    assert root != index
    assert root.lower() != index.lower()


def test_the_same_url_always_gets_the_same_id():
    assert doc_id("https://x.com.tr/p") == doc_id("https://x.com.tr/p")


def test_text_hash_ignores_surrounding_whitespace():
    """Whitespace churn must not read as a content change and trigger re-embedding."""
    assert text_hash("  kâr payı oranı\n") == text_hash("kâr payı oranı")


def test_an_unchanged_document_keeps_its_text_hash():
    assert text_hash("Kampanya 31.12.2026 tarihine kadar geçerlidir.") == text_hash(
        "Kampanya 31.12.2026 tarihine kadar geçerlidir."
    )


def test_only_the_changed_section_gets_a_new_hash():
    """Phase 2 re-embeds per section, so one edit must not invalidate its neighbours."""
    before = ["Katılım şartları", "Kampanya 31.12.2026 tarihine kadar geçerlidir."]
    after = ["Katılım şartları", "Kampanya 31.01.2027 tarihine kadar geçerlidir."]
    assert text_hash(before[0]) == text_hash(after[0])
    assert text_hash(before[1]) != text_hash(after[1])


# ----- domain and asset tests -----

def test_a_subdomain_belongs_to_the_site():
    """Bank PDFs live on asset.* and yukle.* hosts."""
    assert same_site("https://asset.emlakkatilim.com.tr/a.pdf", "emlakkatilim.com.tr")
    assert same_site("https://emlakkatilim.com.tr/a", "emlakkatilim.com.tr")


def test_a_lookalike_domain_does_not_belong_to_the_site():
    """endswith() without a label boundary would accept this."""
    assert not same_site("https://notkuveytturk.com.tr/p", "kuveytturk.com.tr")


def test_a_pdf_is_recognised_from_its_path():
    assert is_pdf("https://x.com.tr/a/form.pdf")
    assert is_pdf("https://x.com.tr/a/FORM.PDF?v=2")
    assert not is_pdf("https://x.com.tr/a/form")


def test_tracking_parameters_are_recognised_by_prefix_and_by_name():
    assert is_tracking_param("utm_source")
    assert is_tracking_param("UTM_Medium")
    assert is_tracking_param("gclid")
    assert not is_tracking_param("category")


# ----- the site registry -----

def test_every_site_slug_names_a_registered_bank():
    """A document and a live price quote must agree about who a bank is. Without
    this the corpus can drift to `vakifkatilim` while the tools say `vakif`."""
    for site in SITES:
        assert get_bank(site.slug) is not None


def test_every_site_display_name_matches_its_bank():
    for site in SITES:
        assert site.display_name == get_bank(site.slug).display_name


def test_every_site_host_is_its_base_url_host():
    """The canonical host must match the base URL, or every fetch redirects and
    the stored URLs are not the ones the server considers canonical. Hayat and
    Dünya redirect www -> no-www; the rest keep www. Verified live 2026-08-09."""
    from urllib.parse import urlsplit
    for site in SITES:
        assert site.host == urlsplit(site.base).hostname
        assert site.base.startswith(f"https://{site.host}")


def test_an_unknown_site_lists_the_valid_ones():
    with pytest.raises(ValueError, match="kuveytturk"):
        get_site("kuveyt-turk")


def test_site_lookup_is_case_insensitive():
    assert get_site("KuveytTurk").slug == "kuveytturk"


def test_all_ten_banks_have_a_site():
    assert len(list_sites()) == 10
    assert len(set(list_sites())) == 10


def test_every_site_declares_a_host_inside_its_own_domain():
    """A typo here would send one bank's crawl at another bank's server."""
    for site in SITES:
        assert same_site(f"https://{site.host}/", site.root_domain)
        assert same_site(site.base, site.root_domain)


def test_the_no_www_sites_are_dunya_and_hayat():
    """Both redirect www -> no-www, so their canonical host has none. This is why
    host folding is per-site rather than global -- verified live 2026-08-09."""
    no_www = {s.slug for s in SITES if not s.host.startswith("www.")}
    assert no_www == {"dunya", "hayat"}


def test_doc_kinds_include_the_kinds_the_pdf_policy_accepts():
    for kind in ("campaign", "product", "fees", "rates", "faq"):
        assert kind in DOC_KINDS
