"""Aynı konuyu tetikleyen paralel sayfalar İKİ tablo oluşturamaz.

Canlı koşuda kanıtlandı: 'kadınlara özel kritik hastalıklar sigortası'
8 saniye arayla iki kez oluştu (…-sigortası ve …-sigortası-2), aynı kaynak
URL'den — PAGE_WORKERS=5 paralelliğinde check-then-act yarışı.
"""
import threading

from dataprep.compare import pipeline


def test_ayni_konu_kilidi_paylasilir():
    a = pipeline._konu_kilidi("Özel Cari Hesap")
    b = pipeline._konu_kilidi("özel   cari hesap")   # büyük/küçük + fazla boşluk
    assert a is b, "aynı konu farklı kilit aldı -> yarış açık kalır"


def test_farkli_konu_farkli_kilit():
    a = pipeline._konu_kilidi("katılma hesabı")
    b = pipeline._konu_kilidi("murabaha finansman")
    assert a is not b, "farklı konular birbirini bloklamamalı"


def test_kilit_gercekten_seri_hale_getirir(monkeypatch):
    """İki iş parçacığı aynı konuda çalışırsa, ikincisi ilki bitmeden giremez."""
    esZamanli = []
    aktif = []
    kilit_sayaci = threading.Lock()

    def sahte(bank, url, topic, match_id, banks):
        with kilit_sayaci:
            aktif.append(1)
            esZamanli.append(len(aktif))
        threading.Event().wait(0.05)
        with kilit_sayaci:
            aktif.pop()

    monkeypatch.setattr(pipeline, "_process_topic_kilitli", sahte)
    tlar = [threading.Thread(target=pipeline._process_topic,
                              args=("b", f"u{i}", "aynı konu", "", []))
            for i in range(4)]
    for t in tlar:
        t.start()
    for t in tlar:
        t.join()
    assert max(esZamanli) == 1, f"aynı konu paralel işlendi: {esZamanli}"


def test_farkli_konular_paralel_kalir(monkeypatch):
    aktif = []
    esZamanli = []
    sayac = threading.Lock()
    basladi = threading.Barrier(3, timeout=5)

    def sahte(bank, url, topic, match_id, banks):
        basladi.wait()                    # üçü de aynı anda içeride olabilmeli
        with sayac:
            aktif.append(1)
            esZamanli.append(len(aktif))
        with sayac:
            aktif.pop()

    monkeypatch.setattr(pipeline, "_process_topic_kilitli", sahte)
    tlar = [threading.Thread(target=pipeline._process_topic,
                              args=("b", "u", f"konu{i}", "", []))
            for i in range(3)]
    for t in tlar:
        t.start()
    for t in tlar:
        t.join()
    assert len(esZamanli) == 3           # barrier açıldıysa üçü de girebildi
