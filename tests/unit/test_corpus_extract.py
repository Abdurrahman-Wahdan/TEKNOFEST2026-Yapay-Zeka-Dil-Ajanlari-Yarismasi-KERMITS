"""HTML into clean, citable sections."""

import pytest

from corpus import clean, extract
from corpus.sites import get_site

pytestmark = pytest.mark.unit

DUNYA = get_site("dunya")
KUVEYT = get_site("kuveytturk")


# ----- cleaners -----

def test_a_link_keeps_its_words_and_loses_its_url():
    """Link syntax is 7.1% of campaign text, of which 5.7% is the URL: tokens
    with no meaning that dilute the vector."""
    assert clean.strip_link_targets(
        "Detaylar [Kampanya Koşulları](https://x.com.tr/kosullar) sayfasında."
    ) == "Detaylar Kampanya Koşulları sayfasında."


def test_an_image_keeps_its_alt_text():
    assert clean.strip_link_targets("![Kuveyt Türk logosu](https://x/l.png)") == (
        "Kuveyt Türk logosu")


def test_the_bank_suffix_leaves_the_title_and_nothing_else():
    """2,298 Kuveyt Türk pages carry this suffix."""
    assert clean.clean_title(
        "Colin's'de Vade Farksız 4 Aya Varan Taksit Fırsatı | Kuveyt Türk Katılım Bankası",
        (KUVEYT.display_name,)) == "Colin's'de Vade Farksız 4 Aya Varan Taksit Fırsatı"


def test_a_title_without_a_suffix_is_untouched():
    assert clean.clean_title("Kâr Payı Oranları", (KUVEYT.display_name,)) == "Kâr Payı Oranları"


@pytest.mark.parametrize("title", ["Detay", "Duyuru Detay", "Blog Detay",
                                   "Fotoğraf Galerisi", "Anasayfa"])
def test_a_template_slot_is_not_a_title(title):
    """~1,100 documents are titled like this. A title naming no document is
    worse than none, because it looks like one."""
    assert clean.is_uninformative_title(title)


@pytest.mark.parametrize("title", ["Kampanya", "Duyuru", "Haber", "Ürün"])
def test_a_bare_section_word_is_not_a_title(title):
    """Measured on a live Emlak build: 10 of 56 documents were titled just
    "Kampanya" because that bank titles every campaign page identically."""
    assert clean.is_uninformative_title(title)


def test_a_real_title_is_not_treated_as_a_template_slot():
    assert not clean.is_uninformative_title("Konut Finansmanı")


def test_a_title_falls_back_to_the_url_slug():
    assert clean.title_from_slug(
        "https://www.kuveytturk.com.tr/kampanyalar/colinsde-vade-farksiz-taksit"
    ) == "Colinsde vade farksiz taksit"


def test_the_slug_fallback_drops_the_page_extension():
    assert clean.title_from_slug(
        "https://www.turkiyefinans.com.tr/tr-tr/Sayfalar/kar-zarar-ortakligi.aspx"
    ) == "Kar zarar ortakligi"


def test_the_dunya_cookie_notice_is_stripped():
    """6,928 characters in 190 of that bank's 272 documents -- 63.8% of
    everything it publishes."""
    text = ("# Kampanya\n\nGerçek içerik burada.\n\n"
            "ÇEREZ KULLANIMINA İLİŞKİN AYDINLATMA METNİ\n\nUzun metin devam eder.\n\n"
            "# Sonraki Başlık\n\nBaşka içerik.")
    result = clean.strip_boilerplate(text, DUNYA.boilerplate)
    assert "ÇEREZ KULLANIMINA" not in result
    assert "Gerçek içerik burada." in result
    assert "Başka içerik." in result


def test_the_old_crawlers_index_is_recognised():
    """It overwrote seven banks' homepages; it must never reach an embedder."""
    assert clean.looks_like_toc("# Kuveyt Türk — Site İçeriği\n\nToplam 2366 sayfa.")


def test_a_link_list_page_is_recognised_as_an_index():
    body = "\n".join(f"- [Sayfa {n}](https://x.com.tr/{n})" for n in range(30))
    assert clean.looks_like_toc(body)


def test_a_real_page_is_not_mistaken_for_an_index():
    assert not clean.looks_like_toc(
        "# Konut Finansmanı\n\nKâr payı oranı ve ödeme planı hakkında bilgi.")


def test_a_navigation_stub_is_a_stub():
    """262 Emlak and 155 Ziraat documents are these."""
    assert clean.is_stub("Ana Sayfa | İletişim")
    assert not clean.is_stub("x" * 300)


# ----- citation anchors -----

HTML = """<html><body>
<h1 id="kampanya-basligi">Kampanya Başlığı</h1>
<p>Giriş paragrafı burada yer alıyor ve yeterince uzun.</p>
<h2 id="katilim-sartlari">Katılım Şartları</h2>
<p>Kampanyaya katılmak için gereken şartlar burada anlatılır.</p>
<h2>Anchorsuz Başlık</h2>
<p>Bu bölümün HTML tarafında bir id değeri yok.</p>
</body></html>"""


def test_heading_anchors_are_read_from_the_raw_html():
    """trafilatura's markdown discards id attributes, so they must come from
    the HTML itself."""
    found = extract.html_headings(HTML)
    assert (1, "Kampanya Başlığı", "kampanya-basligi") in found
    assert (2, "Katılım Şartları", "katilim-sartlari") in found


def test_an_old_style_named_anchor_inside_a_heading_is_found():
    """Older Turkish bank templates put the anchor on a child <a>."""
    found = extract.html_headings('<h2><a name="eski-anchor"></a>Başlık</h2>')
    assert found == [(2, "Başlık", "eski-anchor")]


def test_a_heading_with_an_id_cites_with_that_anchor():
    url = "https://www.kuveytturk.com.tr/kampanyalar/x"
    markdown = ("# Kampanya Başlığı\n\nGiriş paragrafı burada.\n\n"
                "## Katılım Şartları\n\nŞartlar burada anlatılır.\n")
    found = extract.sections(markdown, HTML, url)
    shartlar = [s for s in found if "Katılım" in s.heading_path][0]
    assert shartlar.anchor == "katilim-sartlari"
    assert shartlar.cite_url == f"{url}#katilim-sartlari"


def test_a_heading_without_an_id_cites_the_bare_url_and_invents_no_fragment():
    """An invented anchor 404s silently and still looks like a working citation."""
    url = "https://www.kuveytturk.com.tr/kampanyalar/x"
    markdown = "## Anchorsuz Başlık\n\nBu bölümün id değeri yok.\n"
    found = extract.sections(markdown, HTML, url)
    assert found[0].anchor == ""
    assert found[0].cite_url == url
    assert "#" not in found[0].cite_url


def test_sections_carry_a_heading_path():
    markdown = ("# Kampanya Başlığı\n\nGiriş.\n\n## Katılım Şartları\n\nŞartlar.\n")
    found = extract.sections(markdown, HTML, "https://x.com.tr/p")
    assert found[-1].heading_path == "Kampanya Başlığı > Katılım Şartları"


def test_each_section_hashes_separately_for_surgical_re_embedding():
    markdown = "# Bir\n\nBirinci bölüm metni.\n\n# İki\n\nİkinci bölüm metni.\n"
    found = extract.sections(markdown, HTML, "https://x.com.tr/p")
    assert found[0].text_hash != found[1].text_hash


def test_only_the_changed_section_gets_a_new_hash():
    before = extract.sections("# Bir\n\nEski metin.\n\n# İki\n\nSabit metin.\n",
                              HTML, "https://x.com.tr/p")
    after = extract.sections("# Bir\n\nYeni metin.\n\n# İki\n\nSabit metin.\n",
                             HTML, "https://x.com.tr/p")
    assert before[0].text_hash != after[0].text_hash
    assert before[1].text_hash == after[1].text_hash


def test_an_empty_section_is_not_emitted():
    found = extract.sections("# Boş\n\n\n", HTML, "https://x.com.tr/p")
    assert found == ()


# ----- the whole page -----

def test_a_page_extracts_a_title_text_and_sections():
    page = ("<html><head><title>Konut Finansmanı | Kuveyt Türk Katılım Bankası</title>"
            "</head><body><h1 id='k'>Konut Finansmanı</h1>"
            "<p>Kâr payı oranı ve ödeme planı hakkında ayrıntılı bilgi burada "
            "yer almaktadır ve yeterince uzundur.</p></body></html>")
    result = extract.extract(page, "https://www.kuveytturk.com.tr/kendim-icin/konut",
                             site=KUVEYT)
    assert result["title"] == "Konut Finansmanı"
    assert result["title_source"] == "meta"
    assert "kâr payı" in result["text"].lower()
    assert result["lang"] == "tr"
    assert not result["is_toc"]


def test_a_page_with_a_template_title_falls_back_to_its_slug():
    page = ("<html><head><title>Duyuru Detay</title></head><body>"
            "<p>Bankamızın yeni şube açılışı hakkında duyuru metni burada.</p>"
            "</body></html>")
    result = extract.extract(page, "https://www.kuveytturk.com.tr/duyurular/yeni-sube",
                             site=KUVEYT)
    assert result["title_source"] == "slug"
    assert result["title"] == "Yeni sube"


def test_the_extracted_page_never_carries_a_description():
    """1,991 distinct values across 4,477 documents; the commonest repeated 679
    times. Not reading it makes embedding it impossible."""
    page = ('<html><head><meta name="description" content="Kuveyt Türk Katılım '
            'Bankacılığı alanlarında yatırım faaliyeti gösteren bir bankadır.">'
            '<title>Kampanya</title></head><body><p>Gerçek kampanya metni burada '
            've yeterince uzun bir paragraf oluşturur.</p></body></html>')
    result = extract.extract(page, "https://x.com.tr/kampanyalar/a", site=KUVEYT)
    assert "description" not in result
    assert "yatırım faaliyeti gösteren" not in result["text"]


def test_unparseable_html_yields_no_headings_rather_than_raising():
    assert extract.html_headings("") == []
