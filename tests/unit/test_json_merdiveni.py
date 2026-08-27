"""JSON merdiveni: eksik key tespiti + feedback'li/feedback'siz dönüşüm.

Kullanıcı kararı 2026-08-20: bir JSON tam gelmezse (karakter ya da KEY hatası)
önce modele SOMUT hatayı bildirerek tekrar dene, olmazsa sıcaklık merdiveniyle
sürdür. Sıra: 0.0 normal -> 0.0 feedback -> 0.3 normal -> 0.3 feedback -> ...
"""
from dataprep.compare import bank_agent as B
from dataprep.compare import classify_agent as C
from dataprep.compare import dedup as D


def test_gecerli_json_hatasiz_gecer():
    d, h = B._try_parse('{"offers": true, "sources": []}', ("offers", "sources"))
    assert d == {"offers": True, "sources": []} and h == ""


def test_bozuk_json_somut_hata_dondurur():
    d, h = B._try_parse('{"offers": tru', ("offers",))
    assert d is None and "ayrıştırılamadı" in h


def test_EKSIK_KEY_tespit_edilir():
    """Asıl istenen: JSON geçerli ama beklenen alan yoksa da hata sayılır."""
    d, h = B._try_parse('{"sources": []}', ("offers", "sources"))
    assert d is None
    assert "offers" in h and "eksik" in h.lower()
    assert "sources" in h          # gelen alanlar da bildirilir


def test_json_nesnesi_degilse_hata():
    d, h = B._try_parse('[1, 2, 3]', ("a",))
    assert d is None and "nesne" in h.lower()


def test_gomulu_json_ayiklanir():
    d, h = B._try_parse('bla {"a": 1} son', ("a",))
    assert d == {"a": 1} and h == ""


def test_merdiven_sirasi_feedbacksiz_sonra_feedbackli(monkeypatch):
    """0.0 normal (ilk cevap) -> 0.0 feedback -> 0.3 normal -> 0.3 feedback"""
    cagrilar = []

    class Sahte:
        content = '{"bozuk"'

    def sahte_invoke(_t, msgs, allow_tools=False, start_attempt=0):
        fb = any("şu hata vardı" in getattr(m, "content", "") for m in msgs)
        cagrilar.append((start_attempt, fb))
        if len(cagrilar) >= 4:
            class Ok:
                content = '{"offers": true, "sources": []}'
            return Ok()
        return Sahte()

    monkeypatch.setattr(B, "_invoke_resilient", sahte_invoke)
    d = B._parse_json_ladder([], '{"bozuk"', bekleyen=("offers", "sources"))
    assert d == {"offers": True, "sources": []}
    assert cagrilar[:4] == [(0, True), (1, False), (1, True), (2, False)], cagrilar


def test_feedback_SOMUT_hatayi_tasir(monkeypatch):
    goruldu = []

    def sahte_invoke(_t, msgs, allow_tools=False, start_attempt=0):
        for m in msgs:
            c = getattr(m, "content", "")
            if "şu hata vardı" in c:
                goruldu.append(c)

        class Ok:
            content = '{"offers": true, "sources": []}'
        return Ok()

    monkeypatch.setattr(B, "_invoke_resilient", sahte_invoke)
    B._parse_json_ladder([], '{"sources": []}', bekleyen=("offers", "sources"))
    assert goruldu, "feedback mesajı hiç gönderilmedi"
    assert "offers" in goruldu[0], "somut eksik alan adı taşınmadı"


def test_tum_modullerde_ayni_imza():
    for m in (B, C, D):
        d, h = m._try_parse('{"a": 1}', ("b",))
        assert d is None and "b" in h, m.__name__
