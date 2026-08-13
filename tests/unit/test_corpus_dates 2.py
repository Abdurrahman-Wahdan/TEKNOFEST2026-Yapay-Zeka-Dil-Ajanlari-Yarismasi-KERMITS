"""Campaign validity dates, and the URL taxonomy.

Every date string here is a real shape from the crawled corpus. Of 422 campaigns
carrying an explicit range, 323 had already expired on the day they were
crawled, so getting this wrong means confidently recommending dead offers.
"""

import pytest

from corpus import classify, dates

pytestmark = pytest.mark.unit


# ----- the labelled form -----

def test_the_label_runs_straight_onto_the_date():
    """Kuveyt Türk writes it with no space, and 418 of its campaigns use it."""
    start, end, source = dates.extract("**Kampanya Tarihleri**6.08.2026 - 31.12.2026")
    assert (start, end, source) == ("2026-08-06", "2026-12-31", "label")


def test_a_labelled_date_beats_any_other_date_on_the_page():
    text = ("Yayın tarihi 01.01.2020\n**Kampanya Tarihleri**6.08.2026 - 31.12.2026")
    assert dates.extract(text)[2] == "label"


# ----- numeric ranges -----

def test_a_plain_numeric_range_is_read():
    assert dates.extract("Kampanya 01.08.2025 - 31.12.2025 arasında geçerlidir.") == (
        "2025-08-01", "2025-12-31", "range")


# ----- prose -----

def test_a_shared_year_range_keeps_its_start():
    """"1 Nisan - 31 Aralık 2026": the start states no year of its own. Without
    a rule for this, every such campaign loses its start and looks open-ended."""
    assert dates.extract("Kampanya **1 Nisan – 31 Aralık 2026** tarihlerinde geçerlidir.") == (
        "2026-04-01", "2026-12-31", "prose")


def test_a_shared_year_range_with_an_emoji_heading():
    assert dates.extract("**📢 Kampanya Dönemi:**17 Temmuz – 17 Ağustos 2026") == (
        "2026-07-17", "2026-08-17", "prose")


def test_an_end_only_deadline_is_read():
    start, end, source = dates.extract("Kampanya 31 Aralık 2026 tarihine kadar geçerlidir.")
    assert (start, end) == ("", "2026-12-31")
    assert source == "prose"


def test_the_same_date_twice_is_not_a_range():
    """34 campaigns used to come out beginning and ending on one day."""
    start, end, _ = dates.extract("31 Aralık 2026 ve yine 31 Aralık 2026 geçerli.")
    assert start == ""
    assert end == "2026-12-31"


def test_dates_found_out_of_order_still_yield_a_range():
    """A yearless start makes the scanner meet the end first. Bailing on that
    returned nothing at all for pages that plainly stated their dates."""
    start, end, _ = dates.extract("31 Aralık 2026 tarihine kadar. Başlangıç 1 Nisan 2026.")
    assert start == "2026-04-01"
    assert end == "2026-12-31"


def test_a_date_far_down_the_page_cannot_widen_the_range():
    """Only the first few dates count, so a publication stamp in a footer does
    not turn a one-month campaign into a five-year one."""
    text = ("1 Ağustos 2026 - 31 Ağustos 2026 arası geçerli.\n" + "dolgu\n" * 50
            + "Bu sayfa 3 Ocak 2019 tarihinde yayımlandı.")
    start, end, _ = dates.extract(text)
    assert start == "2026-08-01"
    assert end == "2026-08-31"


def test_turkish_month_spellings_without_diacritics_are_read():
    assert dates.extract("Kampanya 31 Aralik 2026 tarihine kadar.")[1] == "2026-12-31"


def test_no_date_yields_nothing_rather_than_a_guess():
    assert dates.extract("Kampanya koşulları için şubelerimize başvurun.") == ("", "", "")


def test_an_impossible_date_is_not_returned():
    assert dates.extract("Kampanya 31.02.2026 tarihine kadar.")[1] == ""


# ----- activity -----

def test_a_campaign_past_its_end_date_is_not_active():
    assert not dates.is_active("2024-12-31", today="2026-08-08")


def test_a_campaign_ending_today_is_still_active():
    assert dates.is_active("2026-08-08", today="2026-08-08")


def test_a_campaign_with_no_end_date_is_not_treated_as_expired():
    """No deadline stated is not evidence of expiry; hiding these would drop
    campaigns whose page simply never gave one."""
    assert dates.is_active("", today="2026-08-08")


# ----- taxonomy from the URL -----

@pytest.mark.parametrize("url,kind", [
    ("https://www.kuveytturk.com.tr/kampanyalar/kendim-icin/x", "campaign"),
    ("https://www.kuveytturk.com.tr/blog/yasam/x", "blog"),
    ("https://www.kuveytturk.com.tr/hakkimizda/tarihce", "corporate"),
    ("https://www.emlakkatilim.com.tr/tr/kampanyalar/y", "campaign"),
    ("https://www.kuveytturk.com.tr/kendim-icin/finansmanlar/konut", "product"),
    ("https://www.kuveytturk.com.tr/subeler/istanbul", "branch"),
])
def test_the_url_says_what_kind_of_document_it_is(url, kind):
    assert classify.doc_kind(url) == kind


def test_a_pdf_label_beats_the_url():
    """The label was decided from the document; the URL only says where a link
    to it happened to sit."""
    assert classify.doc_kind("https://x.com.tr/hakkimizda/a.pdf", "fees") == "fees"


@pytest.mark.parametrize("url,audience", [
    ("https://www.kuveytturk.com.tr/kendim-icin/kartlar", "bireysel"),
    ("https://www.kuveytturk.com.tr/isim-icin/pos", "ticari"),
    ("https://www.turkiyefinans.com.tr/tr-tr/kobi/x", "kobi"),
    ("https://www.kuveytturk.com.tr/ozel-bankacilik/y", "ozel"),
    ("https://www.kuveytturk.com.tr/blog/z", ""),
])
def test_the_url_says_who_the_document_is_for(url, audience):
    assert classify.audience_of(url) == audience


def test_a_language_prefix_is_never_the_section():
    assert classify.section_of("https://www.emlakkatilim.com.tr/tr/kampanyalar/x") == "kampanyalar"
    assert classify.section_of("https://www.turkiyefinans.com.tr/tr-tr/bireysel/y") == "bireysel"


def test_the_category_is_the_second_segment():
    assert classify.category_of(
        "https://www.kuveytturk.com.tr/kampanyalar/kart-kampanyalari/x") == "kart-kampanyalari"


def test_campaigns_are_rechecked_daily_and_contracts_are_not():
    """Ten sites and 8,500 documents fetched nightly from one address is how a
    crawler gets banned, and a ban looks like ten simultaneous outages."""
    assert classify.refresh_days("campaign") == 1
    assert classify.refresh_days("legal") == 30
    assert classify.refresh_days("blog") > classify.refresh_days("campaign")
