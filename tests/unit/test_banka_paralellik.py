"""Aynı bankadan TEK sayfa, bankalar arası TAM paralellik.

Aynı bankanın iki sayfası aynı anda işlenirse ikisi de aynı konuyu tetikleyip
mükerrer tablo doğurabiliyor (canlı koşuda gözlemlendi). Sınır SAYFA değil
BANKA düzeyinde: her banka kendi kilidini alır, farklı bankalar birbirini
hiç bloklamaz.
"""
import threading

from dataprep.compare import pipeline


def _kos(cift_listesi, esZamanli, aktif, sayac, banka_kilit):
    """run_all'daki _handle'ın kilitleme davranışını birebir taklit eder."""
    def isle(bank):
        with banka_kilit[bank]:
            with sayac:
                aktif[bank] = aktif.get(bank, 0) + 1
                esZamanli.append((bank, aktif[bank]))
            threading.Event().wait(0.03)
            with sayac:
                aktif[bank] -= 1

    tlar = [threading.Thread(target=isle, args=(b,)) for b in cift_listesi]
    for t in tlar:
        t.start()
    for t in tlar:
        t.join()


def test_ayni_banka_seri_isler():
    kilit = {"albaraka": threading.Lock()}
    ez, aktif, sayac = [], {}, threading.Lock()
    _kos(["albaraka"] * 5, ez, aktif, sayac, kilit)
    assert max(n for _, n in ez) == 1, f"aynı banka paralel işlendi: {ez}"


def test_farkli_bankalar_paralel_kalir():
    bankalar = [f"b{i}" for i in range(10)]
    kilit = {b: threading.Lock() for b in bankalar}
    basladi = threading.Barrier(10, timeout=5)
    girenler = []

    def isle(bank):
        with kilit[bank]:
            basladi.wait()          # 10'u da aynı anda içeride olamazsa timeout
            girenler.append(bank)

    tlar = [threading.Thread(target=isle, args=(b,)) for b in bankalar]
    for t in tlar:
        t.start()
    for t in tlar:
        t.join()
    assert len(girenler) == 10, "bankalar arası paralellik bloklandı"


def test_page_workers_seri():
    """SAYFA DÜZEYİNDE TAM SERİ (kullanıcı kararı 2026-08-22).

    Eski test PAGE_WORKERS >= 10 bekliyordu (bankalar arası paralellik
    tasarımı). Kullanıcı mükerrerliğin HİÇBİR koşulda kaçmaması için
    sayfaların tek tek işlenmesini istedi: iki sayfa aynı anda incelenmezse
    aynı konuyu iki kez tetikleyip iki ayrı tablo doğurmaları da imkânsız
    olur. Paralellik kaybı yok denecek kadar azdır — asıl iş sayfa başına
    yapılan 10-BANKA FAN-OUT'udur ve o aynen paralel kalır."""
    assert pipeline.PAGE_WORKERS == 1, "sayfalar seri işlenmeli (mükerrerlik kaçmasın)"


def test_akis_bankalar_arasi_donusumlu(monkeypatch):
    """_all_pages bankaları DÖNÜŞÜMLÜ vermeli — yoksa banka-başına-tek-sayfa
    kuralı tüm işçileri aynı bankaya düşürüp seri çalışmaya zorlar."""
    def sahte(bank, limit):
        for i in range(3):
            yield (i, 3, f"http://{bank}/{i}", "govde", None)

    monkeypatch.setattr(pipeline, "_pages", sahte)
    sira = [b for b, _ in pipeline._all_pages(["a", "b", "c"], None)]
    # ilk üç öğe üç FARKLI bankadan gelmeli
    assert len(set(sira[:3])) == 3, f"dönüşümlü değil: {sira[:6]}"
    assert len(sira) == 9, "sayfa kaybı"


def test_akis_biten_bankayi_dusurur(monkeypatch):
    def sahte(bank, limit):
        n = 1 if bank == "a" else 3
        for i in range(n):
            yield (i, n, f"http://{bank}/{i}", "g", None)

    monkeypatch.setattr(pipeline, "_pages", sahte)
    sira = [b for b, _ in pipeline._all_pages(["a", "b"], None)]
    assert sira.count("a") == 1 and sira.count("b") == 3, sira
