"""AŞAMA 1 hasat hızı — WAF/rate-limit'e takılmayan UYARLANABİLİR sınırlayıcı.

SORUN (ölçüldü): hasat 50 eşzamanlı bağlantıyla iniyor. 9 banka
bunu tolere etti, kuveytturk ETMEDİ — WAF devreye girip IP'yi askıya aldı.
Belirti nettir: TCP bağlantısı AÇILIYOR ama TLS "Client hello"da yanıt gelmiyor,
yani istekler ConnectTimeout'a düşüyor (User-Agent değiştirmek işe yaramadı;
engelleme IP bazlıydı, VPN IP'si değişince anında düzeldi).

NEDEN SABİT BİR SAYI DEĞİL: hangi bankanın ne kadar tolere ettiği önceden
bilinmiyor ve zamanla değişiyor. Elle "kuveytturk=2, diğerleri=50" yazmak
kırılgan bir çözüm olurdu — yarın başka bir banka aynı şeyi yaparsa yine elle
müdahale gerekirdi. Bunun yerine sistem GÜVENLİ HIZI KENDİ BULUR.

ALGORİTMA (dataprep/net_limit.py::AdaptiveSemaphore ile AYNI aile — AIMD,
ağ dünyasında onlarca yıldır kanıtlanmış tıkanıklık kontrolü):

  * Art arda BAŞARI  -> limiti +1 artır (toplamaya dayalı, temkinli).
  * TIMEOUT/bağlantı hatası -> limiti YARIYA indir (çarpımsal, hızlı geri çekil).
    WAF'a yakalanmanın maliyeti (dakikalarca komple blok) yavaş tepkiye değmez.
  * Taban 1'e kadar iner; oradan da hata gelirse FRENLEME (bekleme) devreye girer.

FRENLEME: taban limitte bile art arda hata alınıyorsa bu artık "biraz hızlıyız"
değil, "bloklandık" demektir. Bu durumda hasat kendini DURAKLATIR (üstel
bekleme, 5 dakika tavanlı) ve yoklamaya devam eder. Süreç ÖLMEZ — WAF blokları
tipik olarak 15-60 dakikada düşüyor ve o ana kadarki tüm ilerleme korunur.
Eskiden bu durumda binlerce istek boşa timeout'a gidiyor, koşu "bitmiş" ama
sayfaların çoğu inmemiş oluyordu (kuveytturk: 981 FAIL, 0 yeni sayfa).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

log = logging.getLogger("dataprep.crawl.hiz")

# Başlangıç/tavan: hızlı bankalarda eski davranış korunur (50).
BASLANGIC = int(os.environ.get("CRAWL_HARVEST_CONCURRENCY", "50"))
TAVAN = int(os.environ.get("CRAWL_HARVEST_MAX", str(BASLANGIC)))
TABAN = int(os.environ.get("CRAWL_HARVEST_MIN", "1"))
# Limiti +1 artırmak için gereken ardışık başarı sayısı (limitle orantılı büyür).
BUYUME_ESIGI = int(os.environ.get("CRAWL_GROW_AFTER", "20"))
# Tabanda art arda bu kadar hata -> bloklandık say, frene bas.
BLOK_ESIGI = int(os.environ.get("CRAWL_BLOCK_AFTER", "8"))
FREN_TAVAN = float(os.environ.get("CRAWL_BRAKE_MAX", "300"))    # 5 dk


# BANKA BAŞINA NAZİK PROFİL — WAF'ı ölçülmüş şekilde tetikleyen siteler.
# Uyarlanabilir algoritma zaten güvenli hızı kendi bulur, ama BAŞLANGIÇ
# noktası yüksekse ilk saniyelerde blok yiyebiliyoruz (kuveytturk: 50 ile
# başlayınca 981 ConnectTimeout + IP askıya alma). Bu tablo, bilinen hassas
# siteler için düşük bir başlangıç verir; sistem oradan yukarı yoklar.
#
# İçerik/görsel indirme (dataprep/images.py) de bunu okur — aynı site, aynı
# nezaket. Kullanıcı kararı 2026-08-23: "image indirme koşusunda da
# Kuveyttürk nazik kalmalı".
NAZIK_BANKALAR: dict[str, dict] = {
    # SANİYEDE 1 İSTEK (kullanıcı kararı 2026-08-23). Eşzamanlılık 1'e
    # SABİTLENDİ (baslangic=tavan=1) + istekler arası 1sn gecikme => en fazla
    # ~1 req/sn. Tavan 1 olduğu için uyarlanabilir algoritma YUKARI
    # tırmanmaz; aşağı inecek yer de yok, blok gelirse fren (duraklat-yokla)
    # devreye girer.
    #
    # GEÇMİŞ (ölçülmüş): 50 -> IP bloğu (981 ConnectTimeout). 8'e tırmanınca
    # ilk hatalar. 5'te bir kez 5->2 düşüş. Kullanıcı en güvenli bandı seçti:
    # yavaş ama KESİNTİSİZ aksın, engellenme riski sıfıra yaklaşsın.
    # gecikme YOK, bunun yerine HIZ (istek/saniye) sınırı: art arda iki
    # isteğin başlangıcı arasında en az 1/rps saniye bırakılır. Fark önemli:
    # sabit "gecikme" istek SÜRESİNİN ÜSTÜNE ekleniyordu (1sn istek + 1sn
    # bekleme = 0.5 req/sn), oysa hız sınırı isteğin kendi süresini SAYAR ve
    # gerçekten 1 req/sn verir (kullanıcı kararı 2026-08-23).
    # NOT (2026-08-23): artık TÜM bankalara host başına 1 istek/sn global
    # sınırı uygulanıyor (dataprep/site_hizi.py). Bu tablo yine de duruyor
    # çünkü EŞZAMANLILIĞI da 1'de tutuyor: 1 req/sn ile 5 eşzamanlı bağlantı
    # teknik olarak mümkün ama kuveytturk'te ölçülen blok eşiği düşüktü,
    # bağlantı sayısını da 1'de tutmak en güvenlisi.
    "kuveytturk": {"baslangic": 1, "tavan": 1, "gecikme": None, "rps": 1.0},
}


def banka_profili(slug: str) -> dict:
    """Bir bankanın hız profili: {'baslangic','tavan','gecikme'}.
    Tanımlı değilse genel varsayılanlar döner."""
    return NAZIK_BANKALAR.get(slug, {"baslangic": BASLANGIC, "tavan": TAVAN,
                                      "gecikme": None, "rps": None})


class UyarlanabilirHiz:
    """Hasat için dinamik kapasiteli asenkron semafor + blok freni."""

    def __init__(self, baslangic: int = BASLANGIC, taban: int = TABAN,
                 tavan: int = TAVAN, rps: float | None = None):
        self._limit = max(taban, min(baslangic, tavan))
        self._taban, self._tavan = taban, tavan
        self._aktif = 0
        self._cv = asyncio.Condition()
        self._basari_serisi = 0
        self._tabanda_hata = 0
        self._fren = 0.0                  # bu zamana kadar yeni istek yok
        self._istatistik = {"ok": 0, "hata": 0, "buyume": 0, "kucultme": 0, "fren": 0}
        # HIZ SINIRI (istek/saniye). None -> sınır yok. Sabit bir "gecikme"den
        # farkı: isteğin KENDİ süresini sayar, yani gerçekten rps verir.
        self._min_aralik = (1.0 / rps) if rps else 0.0
        self._son_baslangic = 0.0

    # --- semafor arayüzü ---
    async def __aenter__(self):
        while True:
            bekle = self._fren - time.time()
            if bekle > 0:
                await asyncio.sleep(min(bekle, 5.0))
                continue
            async with self._cv:
                if self._aktif < self._limit:
                    if self._min_aralik:
                        kalan = self._son_baslangic + self._min_aralik - time.time()
                        if kalan > 0:
                            await asyncio.sleep(kalan)
                        self._son_baslangic = time.time()
                    self._aktif += 1
                    return self
                try:
                    await asyncio.wait_for(self._cv.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass

    async def __aexit__(self, *exc):
        async with self._cv:
            self._aktif -= 1
            self._cv.notify()
        return False

    # --- uyarlama ---
    def bildir(self, ok: bool) -> None:
        """Bir isteğin sonucu. Senkron: olay döngüsünü bloklamaz."""
        if ok:
            self._istatistik["ok"] += 1
            self._tabanda_hata = 0
            self._basari_serisi += 1
            gereken = BUYUME_ESIGI + self._limit // 4
            if self._basari_serisi >= gereken:
                self._buyu()
            return

        self._istatistik["hata"] += 1
        self._basari_serisi = 0
        if self._limit > self._taban:
            self._kucult()
            return
        # ZATEN TABANDAYIZ: bu bir hız sorunu değil, blok sinyali.
        self._tabanda_hata += 1
        if self._tabanda_hata >= BLOK_ESIGI:
            self._frene_bas()

    def _buyu(self) -> None:
        if self._limit >= self._tavan:
            self._basari_serisi = 0
            return
        self._limit += 1
        self._basari_serisi = 0
        self._istatistik["buyume"] += 1
        log.info("    [HIZ +] hasat limiti %d -> %d (istekler temiz geçiyor)",
                 self._limit - 1, self._limit)

    def _kucult(self) -> None:
        yeni = max(self._taban, self._limit // 2)
        if yeni == self._limit:
            return
        log.warning("    [HIZ -] hasat limiti %d -> %d (bağlantı hatası — WAF/"
                    "rate-limit olabilir)", self._limit, yeni)
        self._limit = yeni
        self._istatistik["kucultme"] += 1

    def _frene_bas(self) -> None:
        """Tabanda bile hata alıyoruz -> bloklandık. Duraklat, sonra yokla."""
        self._istatistik["fren"] += 1
        sure = min(30.0 * (2 ** (self._istatistik["fren"] - 1)), FREN_TAVAN)
        self._fren = time.time() + sure
        self._tabanda_hata = 0
        log.warning("    [HIZ FREN] taban limitte bile hata sürüyor — site bizi "
                    "ENGELLEMİŞ olabilir. %.0f saniye duraklanıyor, sonra tekrar "
                    "denenecek (ilerleme KORUNUYOR, süreç ölmüyor).", sure)

    @property
    def limit(self) -> int:
        return self._limit

    def ozet(self) -> dict:
        return {"limit": self._limit, "aktif": self._aktif, **self._istatistik}
