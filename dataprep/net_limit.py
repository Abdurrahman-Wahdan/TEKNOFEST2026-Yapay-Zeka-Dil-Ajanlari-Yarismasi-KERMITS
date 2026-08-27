"""UYARLANABİLİR eşzamanlılık sınırlayıcı — tüm senkron LLM çağrı yolları için
PAYLAŞILAN (vlm.py, compare/bank_agent.py, compare/classify_agent.py,
compare/dedup.py; hepsi AYNI süreçte AYNI uzak sunucuyu kullanıyor).

NEDEN SABİT BİR SAYI DEĞİL — ölçülmüş iki gerçek:

  1) Aradaki tünel (localhost.run) bağlantıya ~120 SANİYELİK SABİT ÖMÜR sınırı
     uyguluyor. Kanıt: 100 eşzamanlı istekle yapılan koşuda 28 kopmanın TAMAMI
     121-122s'de gerçekleşti (bizim read timeout'umuz 300s, yani biz kesmiyoruz;
     sunucu bunları 499 = "istemci gitti" olarak logladı). Streaming bunu TEK
     BAŞINA çözmüyor: veri sürekli aksa bile ömür sınırı işliyor.

  2) Üretim süresi eşzamanlılıkla DOĞRUSAL artıyor (GPU paylaşıldığı için).
     Ölçüm (8k girdi + 8192 max_tokens): N=1 -> 53s, N=5 -> 59s, N=10 -> 68s,
     N=20 -> 130s+ (kopma başlıyor).

Yani güvenli eşzamanlılık SABİT değil: sunucunun o anki yüküne, istek
boyutuna, tünelin durumuna göre değişiyor. Sabit bir sayı ya çok düşük kalıp
verimden feragat ettiriyor ya çok yüksek olup kopma üretiyor (boşa GPU + retry).

ALGORİTMA (TCP tıkanıklık kontrolünün aynısı — AIMD, additive increase /
multiplicative decrease; ağ dünyasında onlarca yıldır çalıştığı kanıtlanmış):

  * BAŞARI + istek HIZLI bittiyse  -> limiti YAVAŞÇA artır (+1), ama sadece
    art arda yeterince başarı biriktiyse (gürültüye kanma).
  * KOPMA/timeout görülürse        -> limiti HEMEN YARIYA indir (çarpımsal).
    Tıkanıklığın maliyeti (120s boşa giden GPU) yavaş tepki vermeye değmez.
  * İstek YAVAŞ bittiyse (tehlike bölgesi, ömür sınırına yaklaşıyor) -> artırma
    YAPMA, hatta sınıra çok yaklaştıysa bir tık azalt.

Kullanım (mevcut kodda hiçbir değişiklik gerekmez, `with NET_SEM:` aynen çalışır):

    with NET_SEM:                 # sınır dinamik olarak uygulanır
        r = client.post(...)

İsteğe bağlı geri bildirim (daha isabetli ayar için — vlm.py bunu yapıyor):

    NET_SEM.report(ok=True, duration=12.3)    # başarı, 12.3 saniye sürdü
    NET_SEM.report(ok=False)                  # kopma/timeout
"""
import logging
import os
import threading
import time

log = logging.getLogger("dataprep.net_limit")

# ÖLÇEK REFERANSI (saniye) — SADECE "hızlı mı yavaş mı" ayrımı için; bir zaman
# aşımı ya da kesme sınırı DEĞİL.
#
# Değerin 120 olduğu dönemde "tünel 120s'de keser" varsayılıyordu. SUNUCU
# LOGUYLA ÇÜRÜTÜLDÜ: streaming açıkken sunucu tarafında 5 dakika boyunca TEK
# BİR 499 bile yok, yani bağlantılar tünel tarafından kesilmiyor. 300s: gerçek
# istek sürelerinin (uzun PDF/görsel üretimlerinde 150-200s normal) rahatça
# üstünde, böylece normal bir iş "tehlikeli" sayılıp gereksiz fren yapılmaz.
LIFETIME_LIMIT = float(os.environ.get("NET_LIFETIME_LIMIT", "300"))
# "Hızlı" sayılan süre: bunun altı -> rahatça artırılabilir.
FAST_RATIO = 0.35        # 120s * 0.35 = 42s
# "Doyma" eşiği: bunun üstü -> BÜYÜME durur (ama küçülme YOK; küçülme yalnızca
# gerçek kopmada olur — saf AIMD ilkesi).
DANGER_RATIO = 0.65      # 120s * 0.65 = 78s

# SABİT 25 (kullanıcı kararı, 2026-08-23): uyarlanabilir algoritma devre dışı.
# Bu sınır YALNIZ kendi vLLM sunucumuza gider; bankaların CANLI sitelerine
# giden istekler AYRI ve çok daha katı bir kapıdan geçer
# (dataprep/site_hizi.py — host başına 1 istek/sn).
# min = max = start olduğu için limit ne küçülür ne büyür.
#
# GEÇMİŞ: 8(dinamik) -> 100 -> 50 -> 20 -> 60. 50'de sunucu boğulmuştu, AMA
# asıl sebep eşzamanlılık DEĞİL, bayat keep-alive soketleriydi (nginx
# "400 / 0 byte") + 5s'lik retry fırtınası. Onlar düzeltilince
# (keepalive_expiry=20s, backoff=30s) kapasite ölçüldü: mevcut iş çalışırken
# sunucuya EK 60 eşzamanlı istek atıldı -> 60/60 başarı, ortalama 3.0s, sıfır
# hata. Yani darboğaz sunucu değil bizim limitimizdi (20'de kota hep doluydu).
#
# Eski uyarlanabilir davranışa dönmek için: NET_MIN_LIMIT=2 NET_MAX_LIMIT=40
# NET_START_LIMIT=8 (env ile geri alınabilir, kod silinmedi).
# TOPLAM 25 (kullanıcı kararı). AMA bu sınır SÜREÇ İÇİdir — aşağıdaki
# "TEK SÜREÇ KURALI" notuna bakın. Banka başına BAĞIMSIZ pipeline çalıştığımız
# için aynı anda N süreç var ve her biri kendi 25'ini kuruyordu: gerçek
# eşzamanlılık 25 değil 25xN oluyordu.
#
# ÖLÇÜLDÜ (2026-08-23 04:20): 5 content süreci aynı anda -> sunucu GPU
# kuyruğunda boğuldu, chat isteği 9s'den 47s'ye çıktı ve 503 Service
# Unavailable dönmeye başladı; ziraatkatilim 354/364'te 14 dakika takıldı.
#
# ÇÖZÜM: süreç sayısına GÖRE pay dağıt. NET_TOPLAM_LIMIT toplam bütçedir;
# her süreç kendi payını (toplam / aktif_surec) alır. Böylece kaç banka
# paralel koşarsa koşsun sunucuya giden TOPLAM eşzamanlılık bütçeyi aşmaz.
#
# BÜTÇE: TAVAN 50, dış worker TABANI 25 (kullanıcı kuralı: "25 olsun, en kötü
# bazen 25-50 arasına çıkar"). 48'i TABAN yapmak YANLIŞTI — ölçüldü
# 2026-08-23: 48 eşzamanlıda istekler RemoteProtocolError'a düşüp 121s/243s'de
# patladı, hiçbir sayfa bitmedi. ÖNCEKİ ÖLÇÜM: pipeline TAM YÜK altındayken tek istek 1.1-1.7s, boş
# sunucuda 1200 satırlık akış 3s, tünel 400s'lik akışı kesmeden taşıdı. Yani
# darboğaz sunucu ya da tünel DEĞİL, bizim 25'lik tavanımızdı: dış worker'lar
# (PAGE_WORKERS=25) bütçenin tamamını tüketince sayfa içi ön-eleme havuzları
# sıraya giriyordu -> 28 thread, 4 açık bağlantı, CPU %2.
TOPLAM_LIMIT = int(os.environ.get("NET_TOPLAM_LIMIT", "50"))


def _aktif_surec_sayisi() -> int:
    """Aynı anda LLM'e giden dataprep süreci sayısı (en az 1)."""
    try:
        import subprocess
        cikti = subprocess.run(
            ["pgrep", "-f", "python3? -m dataprep"], capture_output=True,
            text=True, timeout=10).stdout
        n = len([x for x in cikti.split() if x.strip()])
        return max(1, n)
    except Exception:
        return 1


_PAY = max(20, TOPLAM_LIMIT // _aktif_surec_sayisi())
MIN_LIMIT = int(os.environ.get("NET_MIN_LIMIT", "20"))
MAX_LIMIT = int(os.environ.get("NET_MAX_LIMIT", "50"))
START_LIMIT = int(os.environ.get("NET_START_LIMIT", "20"))
# TEK SÜREÇ KURALI: bu sınırlayıcı SÜREÇ İÇİdir (paylaşılan bellek). Aynı
# sunucuya giden işler AYRI process'lere bölünürse her biri kendi limitini
# kurar, birbirlerinin yükünü göremez ve gerçek eşzamanlılık N katına çıkar
# (canlı yaşandı: 5 banka = 5 ayrı process = 5 ayrı NET_SEM). Bu yüzden
# pipeline bankaları TEK process içinde işler:
#     python -m dataprep.content <banka1> <banka2> ...
# Banka-içi ve bankalar-arası paralellik ThreadPoolExecutor ile sağlanır;
# hepsi AYNI NET_SEM'i paylaşır, toplam yük doğru kontrol edilir.
# Limiti +1 artırmak için gereken ardışık "hızlı ve başarılı" istek sayısı.
# Yüksek limitlerde daha temkinli olunur (limitle orantılı büyür).
GROW_AFTER = int(os.environ.get("NET_GROW_AFTER", "5"))
# Bir azaltmadan sonra bu kadar saniye artırma yapılmaz (sistem otursun).
COOLDOWN = float(os.environ.get("NET_COOLDOWN", "20"))
# vlm.py::_READ_TIMEOUT ile AYNI değer (oradan bağımsız okunur ki net_limit
# vlm'e import bağımlılığı kurmasın). Süresi buna YAPIŞAN bir hata "zombi
# bağlantı"dır: sunucu yüklü olduğu için değil, tünel sessizce bıraktığı için
# yanıt gelmemiştir — bu bir tıkanıklık sinyali DEĞİLDİR.
READ_TIMEOUT_HINT = float(os.environ.get("VLM_READ_TIMEOUT", "120"))
# Limit bu süre boyunca dipte (MIN_LIMIT) kalırsa, hız ölçemediğimiz için
# değil ÖLÇECEK VERİ OLMADIĞI için orada kilitlenmiş olabilir -> yokla.
STUCK_AT_FLOOR_AFTER = float(os.environ.get("NET_STUCK_AT_FLOOR_AFTER", "90"))


class AdaptiveSemaphore:
    """Dinamik kapasiteli semafor. `with` ile kullanılır; kapasite çalışma
    anında büyüyüp küçülebilir (klasik threading.Semaphore bunu yapamaz)."""

    def __init__(self, start: int, lo: int, hi: int):
        self._limit = max(lo, min(start, hi))
        self._lo, self._hi = lo, hi
        self._active = 0                  # o an uçuşta olan istek sayısı
        self._cv = threading.Condition()
        self._ok_streak = 0
        self._last_shrink = 0.0
        self._stats = {"ok": 0, "fail": 0, "grow": 0, "shrink": 0, "zombie": 0, "probe": 0}
        self._floor_since = 0.0

    # --- semafor arayüzü ---
    def acquire(self):
        with self._cv:
            while self._active >= self._limit:
                self._cv.wait(timeout=1.0)
                self._probe_if_stuck_at_floor()
            self._active += 1

    def _probe_if_stuck_at_floor(self) -> None:
        """DİPTE TAKILMA YOKLAMASI. Yukarıdaki düzeltme başarı sinyaline
        dayanır; ama HİÇ başarı gelmiyorsa (hepsi zombi timeout) o sinyal de
        üretilemez ve limit dipte kalıcı olarak kilitlenir. Bu durumda tek
        çıkış yolu körlemesine bir yoklamadır: dipte STUCK_AT_FLOOR_AFTER
        saniye geçtiyse limiti +1 aç ve ne olduğuna bak.

        Güvenli: yoklama bir tık (+1), gerçekten tıkanıklık varsa ilk kopmada
        _shrink onu geri alır. Kaybetme riski yok, kazanma ihtimali yüksek."""
        if self._limit > self._lo:
            self._floor_since = 0.0
            return
        now = time.time()
        if not self._floor_since:
            self._floor_since = now
            return
        if now - self._floor_since < STUCK_AT_FLOOR_AFTER:
            return
        self._floor_since = now
        self._stats["probe"] += 1
        self._limit += 1
        self._cv.notify()
        log.warning("    [ESZAMANLILIK ?] limit %d -> %d (dipte %.0fs takıldı — "
                    "yukarı yoklama; kopma olursa geri inilir)",
                    self._limit - 1, self._limit, STUCK_AT_FLOOR_AFTER)

    def release(self):
        with self._cv:
            self._active -= 1
            self._cv.notify()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False

    # --- uyarlama ---
    def report(self, ok: bool, duration: float | None = None) -> None:
        """Bir isteğin sonucunu bildir. `duration` verilirse (saniye) karar
        çok daha isabetli olur: hızlı biten istekler büyümeyi tetikler, ömür
        sınırına yaklaşanlar büyümeyi durdurur."""
        with self._cv:
            if not ok:
                self._stats["fail"] += 1
                self._ok_streak = 0
                # TIKANIKLIK MI, ZOMBİ Mİ? Yarıya inmek SADECE sunucu gerçekten
                # aşırı yüklendiyse doğrudur. Ölçülen durum farklı: tünel
                # bağlantıyı sessizce bırakıyor, istek read timeout'una kadar
                # ASILI kalıyor (kanıt: 20:16-20:21 arası 8 bağlantı = kotanın
                # TAMAMI asılıyken sunucu boştaydı, doğrudan curl'e 0.7s'de
                # yanıt veriyordu). Bunu tıkanıklık sanıp limiti kısmak sunucuyu
                # daha da boş bırakır — yanlış teşhise yanlış tedavi.
                #
                # AYIRT EDİCİ: gerçek tıkanıklıkta istek YAVAŞ biter ama
                # bir şeyler akar; zombide HİÇBİR yanıt gelmez ve süre tam
                # read-timeout değerine yapışır (122s, 120s, 122s...).
                zombi = duration is not None and duration >= READ_TIMEOUT_HINT * 0.95
                if zombi:
                    self._stats["zombie"] += 1
                    self._nudge_down(f"zombi bağlantı ({duration:.0f}s yanıtsız)")
                else:
                    self._shrink("kopma/timeout")
                return

            self._stats["ok"] += 1
            if duration is None:
                self._ok_streak += 1
            elif duration >= LIFETIME_LIMIT * DANGER_RATIO:
                # YAVAŞ ama BAŞARILI istek -> büyüme DUR, ve sınıra çok
                # yaklaştıysa KADEMELİ (-1) küçül.
                #
                # ÖLÇÜLMÜŞ GERÇEK: kopma süreleri 120-131s'de yoğunlaşıyor
                # (315+ örnek), ama 135-190s'de BAŞARIYLA biten istekler de
                # var. Yani tünelin sınırı KESKİN değil, OLASILIKSAL: süre
                # arttıkça kopma ihtimali yükseliyor. Bu yüzden:
                #   - kopma (kesin kanıt)        -> YARIYA in (çarpımsal)
                #   - sınıra yaklaşma (risk)     -> BİR TIK in (-1, kademeli)
                # Kademeli olması önemli: erken denememde bunu da yarıya
                # indiriyordum, limit 8'den 2'ye çöküp kuyruk uzuyor ve daha
                # çok kopma doğuruyordu (kısır döngü).
                self._ok_streak = 0
                if duration >= LIFETIME_LIMIT * 0.9:
                    self._nudge_down(f"istek {duration:.0f}s (kopma sınırına yakın)")
                return
            elif duration <= LIFETIME_LIMIT * FAST_RATIO:
                self._ok_streak += 1          # hızlı -> büyümeye aday
            else:
                # ORTA BÖLGE: eskiden burada _ok_streak sıfırlanıp çıkılıyordu.
                # TASARIM HATASI: büyüme YALNIZCA "hızlı" isteklerle mümkündü,
                # oysa limit dibe (MIN_LIMIT) indiğinde istekler doğal olarak
                # yavaşlar (az paralellik = uzun kuyruk) — yani limit düştükten
                # sonra onu geri yukarı çıkaracak sinyal HİÇ ÜRETİLEMİYORDU.
                # Canlı sonuç: limit 8->2'ye indi ve orada KİLİTLENDİ, sunucu
                # boş beklerken saatte binlerce öğe yerine dakikada 2-3 yanıt.
                # Düzeltme: dipteyken BAŞARI TEK BAŞINA yeterli sinyaldir —
                # istek yavaş da olsa BAŞARIYLA bitiyorsa sunucu ayakta demektir,
                # yukarı yoklamayı hak eder (AIMD'nin "slow probe" karşılığı).
                self._ok_streak += 1 if self._limit <= self._lo else 0
                if self._limit > self._lo:
                    return

            # Yüksek limitlerde daha temkinli büyü (limitle orantılı eşik).
            # DİPTE ise daha ATİK ol: oradan çıkamamak asıl risk (yukarıya bkz).
            if self._limit <= self._lo:
                need, bekle = 2, min(COOLDOWN, 5.0)
            else:
                need, bekle = GROW_AFTER + self._limit // 4, COOLDOWN
            if self._ok_streak >= need and time.time() - self._last_shrink >= bekle:
                self._grow()

    def _grow(self):
        if self._limit >= self._hi:
            self._ok_streak = 0
            return
        self._limit += 1
        self._ok_streak = 0
        self._stats["grow"] += 1
        self._cv.notify()
        log.info("    [ESZAMANLILIK +] limit %d -> %d (istekler hızlı, sistem rahat)",
                 self._limit - 1, self._limit)

    def _shrink(self, reason: str):
        """ÇARPIMSAL azaltma (yarıya) — SADECE gerçek kopma/timeout için.
        Kopma kesin kanıttır: kapasiteyi aştık, hızla geri çekil (AIMD'nin
        'multiplicative decrease' kısmı)."""
        if self._limit <= self._lo:
            return
        yeni = max(self._lo, self._limit // 2)
        if yeni == self._limit:
            return
        log.warning("    [ESZAMANLILIK -] limit %d -> %d (%s)", self._limit, yeni, reason)
        self._limit = yeni
        self._last_shrink = time.time()
        self._stats["shrink"] += 1

    def _nudge_down(self, reason: str):
        """KADEMELİ azaltma (-1) — kopma OLMADAN, sadece risk sinyalinde.
        Yarıya indirmek burada YANLIŞ olurdu: başarılı ama yavaş bir istek
        kapasiteyi aştığımızın KANITI değil, sadece işaretidir. Erken bir
        denemede bunu da çarpımsal yapmıştım; limit 8'den 2'ye çöküyor,
        kuyruk uzuyor ve GERÇEK kopmalar doğuyordu (kısır döngü)."""
        if self._limit <= self._lo:
            return
        log.warning("    [ESZAMANLILIK -1] limit %d -> %d (%s)",
                    self._limit, self._limit - 1, reason)
        self._limit -= 1
        self._last_shrink = time.time()
        self._stats["shrink"] += 1

    @property
    def limit(self) -> int:
        return self._limit

    def snapshot(self) -> dict:
        with self._cv:
            return {"limit": self._limit, "active": self._active, **self._stats}


# Tüm LLM çağrı yollarının PAYLAŞTIĞI tek örnek.
NET_SEM = AdaptiveSemaphore(START_LIMIT, MIN_LIMIT, MAX_LIMIT)


def report(ok: bool, duration: float | None = None) -> None:
    """Modül düzeyinde kısayol — `from dataprep import net_limit;
    net_limit.report(...)` şeklinde kullanılabilir."""
    NET_SEM.report(ok, duration)
