"""Which PDFs get read.

Every filename here is real, taken from the crawled corpus, because the rules
were tuned against that corpus and a rule that only works on invented names is
worth nothing.
"""

import pytest

from corpus.pdf_policy import ACCEPTED_LABELS, LABELS, Decision, decide, from_label, section_of

pytestmark = pytest.mark.unit

KUVEYT = "https://www.kuveytturk.com.tr"


# ----- the documents the agent is asked about -----

@pytest.mark.parametrize("filename", [
    "urun_ve_hizmet_ucretleri.pdf",
    "Adil Katılım_Ücret bilgilendirme formu.pdf",
    "01-02511553_R02_ucretlere-iliskin-bilgilendirme.pdf",
])
def test_a_fee_schedule_is_accepted(filename):
    decision = decide(f"{KUVEYT}/documents/{filename}")
    assert decision.accepted
    assert decision.label == "fees"


@pytest.mark.parametrize("filename", [
    "krediler_kar_oranlari_20-05-2026.pdf",
    "01-02511055_R06_Siftah Kart Kar Orani.pdf",
])
def test_a_rate_table_is_accepted(filename):
    decision = decide(f"{KUVEYT}/documents/{filename}")
    assert decision.accepted
    assert decision.label == "rates"


@pytest.mark.parametrize("filename", [
    "vahesabi_onbilg_formu.pdf",
    "katilma-hesaplari-bilgilendirme-formu._v2.pdf",
    "Mudarebe_Akdi_Musteri_Bilgi_Formu.pdf",
    "ozel-cari-hesap-bilgilendirme-formu.pdf",
])
def test_a_product_information_form_is_accepted(filename):
    """These were the "unclassified" pile that filename-by-eye had written off."""
    assert decide(f"{KUVEYT}/documents/{filename}").accepted


def test_a_faq_is_accepted():
    decision = decide(f"{KUVEYT}/documents/yatirim-hesabi-sss.pdf")
    assert decision.accepted
    assert decision.label == "faq"


# ----- the overlap that matters most -----

@pytest.mark.parametrize("filename", [
    "kk_sozlesmeobf29122025.pdf",
    "Kredi-Karti-Sozlesme-Oncesi-Bilgi-Formu_01.05.2024.pdf",
    "veresiye_sozlesme_oncesi_bilgilendirme_formu-27062024.pdf",
    "taksitli_alisveris_kredisi_sozlesme_oncesi_bilgi_formu-27062024.pdf",
])
def test_a_pre_contractual_information_form_beats_the_contract_rule(filename):
    """A "sözleşme öncesi bilgi formu" is the document that states the profit
    rate and the fees. It is also, by name, a sözleşme -- so allow is checked
    before deny, or the single most useful PDF class is thrown away."""
    decision = decide(f"{KUVEYT}/documents/{filename}")
    assert decision.accepted
    assert decision.decided_by == "rule:allow"


@pytest.mark.parametrize("filename", [
    "dunya-katilim-bankasi-ana-sozlesme-tr.pdf",
    "veresiye_sozlesmesi-28102025.pdf",
    "bankacilik_hizmetleri_sozlesmesi-01122025.pdf",
    "Genel Kredi Sözleşmesi.pdf",
])
def test_a_plain_contract_is_rejected(filename):
    """398 documents, 34.9M characters, and none of them answer a campaign question."""
    decision = decide(f"{KUVEYT}/documents/{filename}")
    assert not decision.accepted
    assert decision.decided_by == "rule:deny"


@pytest.mark.parametrize("filename", [
    "article-of-association.pdf",
    "patriot-act.pdf",
    "W8BEN.pdf",
    "banking-license.pdf",
    "anti-money-laundering-policy-procedure.pdf",
    "WOLFSBERG-questionnarie-CBDDQ.pdf",
    "kisisel-verilerle-ilgili-aydinlatma-metni.pdf",
    "2022-faaliyet-raporu-75.pdf",
])
def test_corporate_and_regulatory_noise_is_rejected(filename):
    assert not decide(f"{KUVEYT}/documents/{filename}").accepted


# ----- link context -----

def test_a_pdf_linked_from_a_product_section_is_accepted():
    """The bank decided where to publish it, which beats any filename guess."""
    decision = decide(f"{KUVEYT}/documents/x123.pdf",
                      referrer=f"{KUVEYT}/kendim-icin/kartlar/kredi-karti")
    assert decision.accepted
    assert decision.decided_by == "rule:section"


def test_a_pdf_linked_only_from_investor_relations_is_rejected():
    decision = decide(f"{KUVEYT}/documents/x123.pdf",
                      referrer=f"{KUVEYT}/yatirimci-iliskileri/raporlar")
    assert not decision.accepted
    assert decision.decided_by == "rule:section"


def test_anchor_text_can_carry_the_decision():
    """Only a third of these files declare a title, but the link text is always
    human-written Turkish saying what the document is."""
    decision = decide(f"{KUVEYT}/documents/f_2211_a.pdf",
                      anchor_text="Ürün ve Hizmet Ücretleri")
    assert decision.accepted
    assert decision.label == "fees"


def test_a_language_prefix_is_not_mistaken_for_a_section():
    """Emlak publishes at /tr/kampanyalar; the section is kampanyalar."""
    assert section_of("https://www.emlakkatilim.com.tr/tr/kampanyalar/x") == "kampanyalar"
    assert section_of("https://www.turkiyefinans.com.tr/tr-tr/bireysel/x") == "bireysel"
    assert section_of("https://www.kuveytturk.com.tr/kampanyalar/x") == "kampanyalar"


def test_a_regulator_pdf_is_accepted_when_a_product_page_links_it():
    """Third-party hosts are reached through link context, never by allowlist."""
    decision = decide("https://www.tkbb.org.tr/standart-12.pdf",
                      referrer=f"{KUVEYT}/kendim-icin/finansmanlar")
    assert decision.accepted


# ----- abstaining -----

def test_an_unjudgeable_pdf_is_sent_to_the_classifier_not_dropped():
    """The 367-document pile. Dropping it silently loses real rate documents."""
    decision = decide(f"{KUVEYT}/documents/kpo.pdf")
    assert decision.needs_model
    assert not decision.accepted


def test_an_ambiguous_pdf_carries_a_reason():
    assert "model" in decide(f"{KUVEYT}/documents/kbis_murabaha.pdf").reason


# ----- model verdicts -----

@pytest.mark.parametrize("label", sorted(ACCEPTED_LABELS))
def test_an_accepted_label_is_accepted(label):
    assert from_label(label).accepted


@pytest.mark.parametrize("label", ["contract", "corporate", "regulatory", "privacy", "other"])
def test_a_rejected_label_is_rejected(label):
    decision = from_label(label)
    assert not decision.accepted
    assert decision.decided_by == "model"


def test_a_label_outside_the_closed_set_is_not_accepted():
    """A model that answers with something else has not classified the document,
    and guessing on its behalf is how a contract reaches the campaign collection."""
    decision = from_label("this looks like a fee schedule to me")
    assert not decision.accepted
    assert "not one of the allowed labels" in decision.reason


def test_an_empty_label_is_not_accepted():
    assert not from_label("").accepted


def test_a_label_is_matched_regardless_of_case_and_padding():
    assert from_label("  Fees ").accepted


def test_every_accepted_label_is_a_known_label():
    assert ACCEPTED_LABELS <= set(LABELS)


def test_a_decision_records_who_made_it():
    """So a wrong call is auditable and reversible without re-fetching."""
    for decision in (decide(f"{KUVEYT}/d/ucretler.pdf"),
                     decide(f"{KUVEYT}/d/patriot-act.pdf"),
                     from_label("fees")):
        assert isinstance(decision, Decision)
        assert decision.decided_by
        assert decision.reason
