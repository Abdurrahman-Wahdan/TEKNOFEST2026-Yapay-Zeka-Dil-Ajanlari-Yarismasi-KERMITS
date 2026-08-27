"""Karşılaştırma hattının retrieval araçları.

Banka-scoped olanların üçü (search_bank, expand_chunk, read_full_page) artık
`corpus/search.py`'de: aynı araçları canlı banka uzmanları da kullanıyor
(agents/shared/bank_tools.py) ve Türkçe metin, süresi-geçmiş kuralı, metadata
okuması ile parça budama TEK yerde tanımlı olmalı. Buradan yeniden dışa
aktarılıyorlar, böylece bank_agent.py'nin importları değişmiyor.

Qdrant 'campaigns' koleksiyonunu, metadata.bank filtresiyle arar. Süresi geçmiş
kaynaklar (metadata.gecerlilik_bitis damgalıysa) elenir — bu tarih KIYASI CANLI
yapılır (bugüne göre), metinden regex'le veri çekme değil; content.py'nin
çıkardığı yapılandırılmış tarihi karşılaştırıyoruz.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

# Testler ve canlı süpervizör bu üç yardımcıyı retrieval üzerinden
# import ediyor; tek gerçek kaynakları corpus/search.py.
from corpus.search import _end_date, _expired, _source_url  # noqa: F401

from config import tunnel
from corpus import dates
from embeddings import get_embedding
from vector_stores.client import get_qdrant_client

log = logging.getLogger(__name__)


# --- Qdrant payload alan adi eslemesi -------------------------------------
# 'campaigns' koleksiyonu URL'i TEK bir 'url' alaninda tutmuyor; icerik tipine
# gore farkli alanlarda tutuyor (page->source_url, pdf->pdf_url, image->
# gorsel_url) ve tarihleri 'campaign_start/campaign_end' adiyla yaziyor
# (content.py/pages.py frontmatter adlandirmasi). Retrieval bu adlari tek bir
# kanonik sozlesmeye cevirir; LLM yine SADECE point_id gorur, URL yazmaz.
_URL_ALANLARI = ("url", "source_url", "pdf_url", "gorsel_url", "source_page", "parent")


def _kanonik_url(meta: dict) -> str:
    """Icerik tipinden bagimsiz olarak dokumanin kendi URL'ini dondurur."""
    for k in _URL_ALANLARI:
        v = meta.get(k)
        if v:
            return str(v)
    return ""


def _kanonik_tarih(meta: dict) -> tuple[str, str, str]:
    """(baslangic, bitis, validity_status) — iki adlandirmayi da kabul eder."""
    bas = meta.get("gecerlilik_baslangic") or meta.get("campaign_start") or ""
    bit = meta.get("gecerlilik_bitis") or meta.get("campaign_end") or ""
    st = meta.get("validity_status") or meta.get("campaign_status") or ""
    return str(bas), str(bit), str(st)


def _kaydet_ve_isaretle(points, point_meta: dict | None) -> dict:
    """Okunan chunk'larin point_id'lerini point_meta'ya isler ve
    chunk_index -> point_id haritasi dondurur. Boylece read_more/
    read_full_page ile okunan icerik de KAYNAK GOSTERILEBILIR olur —
    aksi halde ajanin elinde gecerli bir index kalmaz, URL yazmaya
    calisir ve kaynak sessizce dusrulurdu."""
    harita = {}
    for p in points:
        meta = (p.payload or {}).get("metadata", {}) or {}
        pid = str(p.id)
        idx = meta.get("chunk_index", 0)
        harita[idx] = pid
        if point_meta is not None:
            bas, bit, st = _kanonik_tarih(meta)
            point_meta[pid] = {
                "url": _kanonik_url(meta),
                "gecerlilik_baslangic": bas,
                "gecerlilik_bitis": bit,
                "validity_status": st,
            }
    return harita


def _url_kosulu(url: str) -> models.Filter:
    """URL'i HANGI alanda tutuluyorsa orada esler (page/pdf/image farkli alan
    kullaniyor) — tek bir 'metadata.url' alani yok."""
    return models.Filter(should=[
        models.FieldCondition(key=f"metadata.{k}", match=models.MatchValue(value=url))
        for k in _URL_ALANLARI
    ])

COLLECTION = os.environ.get("QDRANT_COLLECTION_CAMPAIGNS", "campaigns")

_embed = None
_client = None
_lock = threading.Lock()   # 10 subagent aynı anda ilk çağrıyı yapabilir; tekil yükleme
EMBED_TIMEOUT = float(os.environ.get("COMPARE_EMBED_TIMEOUT", "30"))


def _shared():
    """Paylaşılan embedder + Qdrant istemcisi.

    embedder ARTIK KALICI SAKLANMAZ: get_embedding() kendi önbelleğini
    (model, VLLM_BASE_URL) anahtarıyla tutuyor, yani tünel adresi değişince
    OTOMATİK olarak taze bir istemci döner. Burada saklamak, tünel
    değiştiğinde retry'ın sonsuza dek ölü adrese gitmesi demekti (kanıtlı
    2026-08-20: sunucu sağlıkken 09:00-10:11 arası sürekli 503)."""
    global _client
    with _lock:
        if _client is None:
            _client = get_qdrant_client()
        client = _client
    return get_embedding(), client


def _embed_query_safe(embedder, query: str, task: str | None = None):
    """embed_query'yi SABİT SÜRELİ bir duvarla çağırır. PyTorch MPS (Apple GPU)
    eşzamanlı erişimde bazen SESSİZCE ÇÖKMÜYOR, DONUYOR — _lock içindeyken bu
    olursa TÜM bankalar (aynı kilidi paylaştıkları için) sonsuza kadar bekler
    (kanıtlı: 869 saniye CPU %0, hiçbir log yok). Ayrı bir daemon thread'de
    çalıştırıp EMBED_TIMEOUT'ta sonuç gelmezse hata fırlatırız — kilit normal
    şekilde serbest kalır (with bloğu), donan thread yalnız kendi başına
    (daemon) sürüp gider, süreci bloklamaz.

    `task`: SADECE gerçek arama SORGULARI için verilir (indekslenen belge/pasaj
    metinleri için değil) — Qwen3-Embedding modeli asimetrik retrieval'da sorgu
    tarafına bir görev talimatı öneki eklenmesini bekliyor, yoksa (kanıtlı) çok
    benzer/ayırt edici olmayan benzerlik skorları üretiyor."""
    text = f"Instruct: {task}\nQuery: {query}" if task else query
    q: queue.Queue = queue.Queue(maxsize=1)

    def _work():
        try:
            q.put(("ok", embedder.embed_query(text)))
        except Exception as exc:                    # noqa: BLE001
            q.put(("err", exc))

    threading.Thread(target=_work, daemon=True).start()
    try:
        status, val = q.get(timeout=EMBED_TIMEOUT)
    except queue.Empty:
        raise RuntimeError(
            f"Embedding {EMBED_TIMEOUT}s içinde tamamlanmadı (GPU donmuş olabilir, "
            "bu arama atlandı).") from None
    if status == "err":
        raise val
    return val


RESULTS_PER_CALL = 5   # sabit grup boyutu — model sayı seçmez


def _apply_mark(useful: list, not_useful: list, marked: set, discarded: set) -> str:
    """useful/not_useful kararını uygular — hem mark_useful'dan hem search_bank'ın
    içine gömülü kararlardan çağrılır (aynı mantık, tek yerden). SON karar geçerli:
    adım sayısı arttıkça önce 'kalsın' dediğine sonra 'silinsin' diyebilir (ya da
    tersi)."""
    useful, not_useful = list(useful or ()), list(not_useful or ())
    new_useful = [p for p in useful if p not in marked]
    discarded.difference_update(useful)
    marked.update(useful)
    new_discard = [p for p in not_useful if p not in discarded]
    marked.difference_update(not_useful)
    discarded.update(new_discard)
    parts = []
    if new_useful:
        parts.append(f"{len(new_useful)} sonuç kalıcı işaretlendi (korunacak).")
    if new_discard:
        parts.append(f"{len(new_discard)} sonuç gereksiz işaretlendi (bir sonraki "
                      "turda geçmişten silinecek).")
    return " ".join(parts)


class _SearchArgs(BaseModel):
    query: str = Field(description="Arama sorgusu — doğal dil, Türkçe. Her çağrı "
                        "en alakalı 5 sonucu getirir. AYNI query'yi next=false ile "
                        "tekrar göndermek BİREBİR AYNI 5 sonucu döner (deterministik, "
                        "yenilik gelmez) — bunun yerine ya FARKLI bir query yaz, ya da "
                        "aynı query için SONRAKİ 5 sonucu görmek istiyorsan next=true "
                        "gönder.")
    intent: str = Field(description="Bu aramayla TAM OLARAK NEYİ bulmaya çalıştığını "
                        "kısa bir cümleyle belirt. Arama embedding tabanlıdır ve bu "
                        "niyet cümlesi arama kalitesini doğrudan etkiler — boş "
                        "bırakma, her çağrıda güncelle.")
    next: bool = Field(default=False, description="True ise: bu AYNI query için bir "
                        "önceki çağrıda görmediğin SONRAKİ 5 sonucu getirir (aynı "
                        "sorgunun derinleştirilmesi — sayfalama). False (varsayılan) "
                        "ise en baştan en alakalı 5 sonucu getirir. Sadece query "
                        "değişmeden tekrar arayacaksan next=true kullan; farklı bir "
                        "query yazıyorsan next=false bırak.")
    useful: list[str] = Field(default_factory=list, description="OPSİYONEL: bir "
                        "ÖNCEKİ search_bank sonucundan gerçekten kullanışlı bulduğun "
                        "point_id'ler — bu aramayla AYNI ÇAĞRIDA işaretleyebilirsin, "
                        "ayrı bir mark_useful adımı harcamana gerek kalmaz.")
    not_useful: list[str] = Field(default_factory=list, description="OPSİYONEL: bir "
                        "ÖNCEKİ search_bank sonucundan gereksiz/konu dışı bulduğun "
                        "point_id'ler — aynı çağrıda işaretlenip hemen silinir.")


def tohum_point_id(bank: str, url: str) -> str | None:
    """Bir sayfanin URL'ini o sayfanin ILK chunk'inin point_id'sine cevirir.

    Amac: seed yonlendirmesinde modele URL VERMEMEK. Model yalnizca index
    (point_id) gorur; URL cozumu tamamen kodda kalir. Bulunamazsa None doner —
    cagiran yonlendirmeyi hic vermez (yanlis id ile model mesgul edilmez)."""
    if not url:
        return None
    try:
        _, client = _shared()
        flt = models.Filter(must=[
            models.FieldCondition(key="metadata.bank",
                                   match=models.MatchValue(value=bank)),
            _url_kosulu(url),
        ])
        points, _ = client.scroll(collection_name=COLLECTION, scroll_filter=flt,
                                   limit=200, with_payload=True)
    except Exception:
        return None
    if not points:
        return None
    ilk = min(points, key=lambda p: ((p.payload or {}).get("metadata", {}) or {})
                                     .get("chunk_index", 0))
    return str(ilk.id)


def make_bank_search_tool(bank: str, marked: set, discarded: set,
                            point_meta: dict | None = None) -> StructuredTool:
    """Bu bankaya SABİTLENMİŞ bir arama tool'u üretir (subagent bankayı seçemez).
    `marked`/`discarded`: mark_tool ile PAYLAŞILAN kümeler — bu çağrıya gömülü
    useful/not_useful kararı da aynı yere yazılır. `_offsets`: aynı query'nin
    'next=true' ile derinleştirilmesi için sorgu başına ilerleme takibi (closure).
    `point_meta`: OPSİYONEL, çağıranla PAYLAŞILAN dict — görülen HER point_id
    (useful/not_useful kararından BAĞIMSIZ) için url+tarih bilgisini kalıcı
    kaydeder; marked/discarded'a (context-trim amaçlı, geçici) HİÇ dokunmaz —
    ayrı bir amaç için (nihai kaynak/tarih çözümleme) hiç silinmeyen bir kayıt."""
    _offsets: dict[str, int] = {}
    point_meta = point_meta if point_meta is not None else {}

    def _run(query: str, intent: str = "", useful: list[str] = (),
              not_useful: list[str] = (), next: bool = False) -> str:
        mark_note = _apply_mark(useful, not_useful, marked, discarded)
        key = query.strip().lower()
        offset = (_offsets.get(key, 0) + RESULTS_PER_CALL) if next else 0
        _offsets[key] = offset
        embedder, client = _shared()
        # PyTorch MPS (Apple GPU) eşzamanlı thread'lerden çağrılınca sessizce
        # çöküyor (traceback'siz) — 10 subagent aynı anda embed edebileceği için
        # GPU çağrısını SERİLEŞTİR. Embedding hızlı; darboğaz zaten LLM çağrıları,
        # onlar kilitlenmeden paralel kalır. _embed_query_safe: DONMAYA karşı da
        # (ör. Mac uykuya geçip GPU context'i toparlanmazsa) sabit süreli duvar.
        # `intent`: statik/sabit bir talimat metni BİZ yazmıyoruz — modelin kendi
        # ifade ettiği arama niyeti kullanılıyor (asimetrik retrieval talimatı).
        with _lock:
            vector = _embed_query_safe(embedder, query, task=intent or None)
        flt = models.Filter(must=[models.FieldCondition(
            key="metadata.bank", match=models.MatchValue(value=bank))])
        hits = client.query_points(
            collection_name=COLLECTION, query=vector, query_filter=flt,
            limit=RESULTS_PER_CALL, offset=offset, with_payload=True).points
        out = []
        for h in hits:
            p = h.payload or {}
            meta = p.get("metadata", {}) or {}
            bas, end, status = _kanonik_tarih(meta)
            if end and not dates.is_active(end):        # süresi geçmiş -> gösterme
                continue
            if not end and status == "suresi_gecmis":    # tarihsiz ama kesin bitmiş
                continue
            point_id = str(h.id)
            url = _kanonik_url(meta)
            point_meta[point_id] = {
                "url": url,
                "gecerlilik_baslangic": bas,
                "gecerlilik_bitis": end,
                "validity_status": status,
            }
            out.append({
                "point_id": point_id, "url": url, "type": meta.get("type", ""),
                # KIRPMA YOK (kullanıcı kararı 2026-08-19): chunk TAM verilir.
                # Eskiden 600 karakterde kesiliyordu; model yarım kalan bilgiyi
                # görüp read_more ile tamamlamak zorunda kalıyordu (fazladan
                # tur + eksik kanıt riski). Bağlam sınırı uzak sunucunun kararı.
                "text": (p.get("page_content") or "").strip(),
                "gecerlilik_bitis": end,
            })
        if not out and not hits:
            body = ("Sonuç yok" + (" (bu query için artık daha fazla sonuç kalmadı, "
                     "next ile ilerleme sınırına ulaştın)" if next else
                     " (bu banka için bu sorguyla hiçbir güncel içerik bulunamadı)")
                     + ".")
        elif not out:
            body = "Bu gruptaki sonuçların hepsi süresi geçmiş kaynaklıydı, gösterilmedi."
        else:
            body = "\n---\n".join(
                f"[{i+1}] point_id={o['point_id']}\n{o['text']}"
                for i, o in enumerate(out))
        return f"{mark_note}\n\n{body}" if mark_note else body

    return StructuredTool.from_function(
        func=_run, name="search_bank", args_schema=_SearchArgs,
        description=(f"'{bank}' katılım bankasının KENDİ içeriğinde ara (yalnız bu "
                     f"bankada, başkasında değil). Her çağrı 5 sonuç getirir — kaç "
                     f"sonuç istediğine SEN karar vermezsin. AYNI query'yi next=false "
                     f"ile tekrar göndermek BİREBİR AYNI 5 sonucu döner (deterministik, "
                     f"yenilik gelmez). Aynı konuda SONRAKİ 5 sonucu görmek istiyorsan "
                     f"query'yi DEĞİŞTİRMEDEN next=true gönder (sayfalama). Farklı bir "
                     f"konuya geçiyorsan yeni bir query yaz, next=false bırak. Süresi "
                     f"geçmiş kampanyalar zaten gösterilmez. Önceki sonuçlar için "
                     f"useful/not_useful kararını da BU çağrıya gömebilirsin. Arama "
                     f"embedding (anlam vektörü) tabanlıdır — intent alanına ne "
                     f"aradığını yazman arama isabetini gerçekten etkiler, formalite "
                     f"değildir."))

# BİLİNÇLİ OLARAK bağımsız bir mark_useful tool'u YOK. Marking'in tek işlevi
# gelecekteki context-trim'de bir chunk'ı korumak — son turda (artık arama
# yapılmayacaksa) zaten "gelecek" kalmıyor, marking'in hiçbir anlamı olmaz.
# Bağımsız bir tool olarak var olduğunda model onu tek başına, boş boş art
# arda çağırıp döngüye giriyordu (kanıtlı bug, _tool_limit_hits.jsonl'de 50
# çağrılık örnek). O yüzden marking SADECE search_bank'ın useful/not_useful
# alanlarına gömülü — üçüncü, "ilerlemesiz" bir tool-çağrı yolu fiziksel
# olarak yok. Uygulama _apply_mark() üzerinden, search_bank'ın _run'ı içinde.


class _NearbyArgs(BaseModel):
    point_id: str = Field(description="search_bank sonucunda gördüğün point_id.")
    direction: str = Field(default="down", description="Gezinme yönü: 'up' (1 adım önceki chunk) veya 'down' (1 adım sonraki chunk).")


def make_read_more_tool(bank: str, point_meta: dict | None = None) -> StructuredTool:
    """Ajan bir chunk'ı slide etmek isterse sadece 1 adım UP (önceki) veya 1 adım DOWN (sonraki) getirir."""

    def _run(point_id: str, direction: str = "down") -> str:
        _, client = _shared()
        try:
            pts = client.retrieve(collection_name=COLLECTION, ids=[point_id], with_payload=True)
        except Exception as exc:
            return f"HATA: point_id okunamadı ({exc})."
        if not pts:
            return "Bu point_id için chunk bulunamadı (yanlış id olabilir)."
        meta = (pts[0].payload or {}).get("metadata", {}) or {}
        idx = meta.get("chunk_index", 0)
        url = _kanonik_url(meta)
        target_idx = (idx - 1) if direction.lower() == "up" else (idx + 1)
        flt = models.Filter(must=[
            models.FieldCondition(key="metadata.bank", match=models.MatchValue(value=bank)),
            _url_kosulu(url),
            models.FieldCondition(key="metadata.chunk_index", match=models.MatchValue(value=target_idx)),
        ])
        points, _ = client.scroll(collection_name=COLLECTION, scroll_filter=flt,
                                   limit=200, with_payload=True)
        harita = _kaydet_ve_isaretle(points, point_meta)
        rows = sorted(
            ((p.payload or {}).get("metadata", {}).get("chunk_index", 0),
             (p.payload or {}).get("page_content", "")) for p in points)
        if not rows:
            return "Komşu chunk bulunamadı."
        return "\n\n".join(
            f"[point_id={harita.get(i, '')} chunk_index={i}]\n{t}"
            for i, t in rows if t.strip())

    return StructuredTool.from_function(
        func=_run, name="read_more", args_schema=_NearbyArgs,
        description=("search_bank sonucundaki bir chunk cümle/bilgi ortadan kesiliyormuş "
                     "gibi görünüyorsa, o sonucun point_id'sini vererek dokümanın HEMEN "
                     "öncesini/sonrasını (before/after kadar komşu chunk, doküman "
                     "sırasına göre) okuyabilirsin — TÜM sayfayı okumaktan "
                     "(read_full_page) daha hedefli, PDF çok uzunsa daha ucuz bir yol. "
                     "Yetmezse before/after'ı büyütüp tekrar çağırabilirsin."))


class _FullPageArgs(BaseModel):
    point_id: str = Field(description="search_bank sonucunda gördüğün point_id — "
                                       "o chunk'ın ait olduğu sayfanın TAMAMI okunur.")


def make_full_page_tool(bank: str, point_meta: dict | None = None) -> StructuredTool:
    """search_bank sadece bir chunk (parça, ~600 karakter) döndürür — sayfanın
    KENDİSİ o parçada kesilmiş/eksik görünüyorsa, agent bu tool'la o point_id'nin
    TÜM chunk'larını (chunk_index sırasıyla) birleştirilmiş, tam metin olarak
    okuyabilir. Banka sabit (closure) — sadece kendi bankasının sayfalarını okur."""

    def _run(point_id: str) -> str:
        _, client = _shared()
        # URL'i LLM DEGIL KOD cozer: model yalnizca index (point_id) takip eder,
        # URL hic gormez -> hem token tasarrufu hem halusinasyon riski sifir.
        try:
            pts = client.retrieve(collection_name=COLLECTION, ids=[point_id],
                                   with_payload=True)
        except Exception as exc:
            return f"HATA: point_id okunamadı ({exc})."
        if not pts:
            return "Bu point_id için chunk bulunamadı (yanlış id olabilir)."
        url = _kanonik_url((pts[0].payload or {}).get("metadata", {}) or {})
        if not url:
            return "Bu chunk'ın sayfa URL'i kayıtlı değil, tam sayfa okunamıyor."
        flt = models.Filter(must=[
            models.FieldCondition(key="metadata.bank", match=models.MatchValue(value=bank)),
            _url_kosulu(url),
        ])
        points, _ = client.scroll(collection_name=COLLECTION, scroll_filter=flt,
                                   limit=200, with_payload=True)
        if not points:
            return "Bu sayfa için chunk bulunamadı (başka bankaya ait olabilir)."
        harita = _kaydet_ve_isaretle(points, point_meta)
        rows = sorted(
            ((p.payload or {}).get("metadata", {}).get("chunk_index", 0),
             (p.payload or {}).get("page_content", "")) for p in points)
        return "\n\n".join(
            f"[point_id={harita.get(i, '')}]\n{t}" for i, t in rows if t.strip())

    return StructuredTool.from_function(
        func=_run, name="read_full_page", args_schema=_FullPageArgs,
        description=("search_bank bir sayfanın yalnızca KÜÇÜK BİR PARÇASINI (chunk) "
                     "getirir; kesilmiş/yarım/yetersiz görünüyorsa bu tool'la o "
                     "sonucun point_id'sini vererek ait olduğu sayfanın/PDF'in TÜM "
                     "parçalarını birleştirilmiş, TAM metin olarak okuyabilirsin."))


# --- tablo havuzu araması (classify_agent'ın aracı) -------------------------
# Tablo havuzu büyüdükçe (yüzlerce olabilir) TÜMÜNÜ tek prompt'a sığdırmak
# imkansız — search_bank ile aynı felsefe: embedding bazlı arama, LLM karar
# verene kadar özgürce (farklı sorgularla) tekrar arayabilir. KALICI: hafıza-içi
# değil, Qdrant'ta ayrı bir koleksiyon — süreç kaç kez restart olursa olsun
# (bu gece çok oldu) yeniden embed etmeye gerek kalmaz, tek gerçek kaynak budur.
import uuid

TABLES_COLLECTION = os.environ.get("QDRANT_COLLECTION_TABLES", "compare_tables")
_TABLE_NS = uuid.UUID("6f9c6e2e-6b7a-4b7a-9c1e-3a2f7b8d5e10")   # id->UUID için sabit namespace
_tables_collection_ready = False
_tables_collection_lock = threading.Lock()


def _table_point_id(table_id: str) -> str:
    return str(uuid.uuid5(_TABLE_NS, table_id))


def _ensure_tables_collection() -> None:
    global _tables_collection_ready
    if _tables_collection_ready:
        return
    with _tables_collection_lock:
        if _tables_collection_ready:
            return
        _, client = _shared()
        if not client.collection_exists(TABLES_COLLECTION):
            client.create_collection(
                collection_name=TABLES_COLLECTION,
                vectors_config=models.VectorParams(size=1024, distance=models.Distance.COSINE))
        _tables_collection_ready = True


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- indeksleme dayanikliligi -------------------------------------------------
_INDEKS_BACKOFF_MAX = float(os.environ.get("INDEX_BACKOFF_MAX", "30"))


def _indeks_kalici_hata(exc: Exception) -> bool:
    """Retry'ın anlamsız olduğu hatalar (bank_agent::_is_permanent ile AYNI
    karar). 400/403 KALICI DEĞİL: tünel soketi bayatlayınca nginx isteği
    reddediyor ama saniyeler sonra aynı istek 200 dönüyor."""
    s = str(exc)
    return any(k in s for k in ("401", "404", "413", "422", "BadRequest"))


def _dayanikli(cagri, ne: str):
    """Bir Qdrant/embedding çağrısını SINIRSIZ retry ile yürütür.

    NEDEN: index_table tek denemede pes ediyordu; tünel değişiminin tam denk
    geldiği anda tablo diske yazılıp arama havuzuna GİRMİYORDU. İndekssiz tablo
    mükerrerlik kontrolünde GÖRÜNMEZ olur ve aynı konuda ikinci bir tablo
    açılır (kasko sigortası 4 kez oluştu). Her hatada tünel URL'i tazelenir.

    RETRY SINIRSIZDIR. Daha önce sınırsız retry süreci 2 saat kilitlemişti
    (240 retry, CPU %0) ama asıl sebep retry değil, EMBEDDING İSTEMCİSİNİN
    ÖNBELLEKTE ESKİ URL ile kalmasıydı: tünel 08:00'da değişti, tunnel.refresh
    settings'i güncelledi, fakat önbellekteki istemci ölü adrese istek atmaya
    devam etti — sunucu sağlıklıyken saatlerce 503 alındı. Önbellek anahtarına
    URL eklenerek (embeddings/providers/remote_provider.py) kökten çözüldü;
    artık refresh sonrası İLK denemede yeni adrese gidilir."""
    delay = 1.0
    deneme = 0
    while True:
        deneme += 1
        try:
            return cagri()
        except Exception as exc:
            if _indeks_kalici_hata(exc):
                raise
            # ÖNCE tünel kontrolü, SONRA yeniden deneme (kullanıcı kararı
            # 2026-08-20): hatanın en olası sebebi tünel adresinin değişmesi.
            # BAĞLANTI GÜVENLİĞİ (kullanıcı kararı 2026-08-22): hata alan
            # bağlantıya geri dönülmez — LLM/embedding havuzu tamamen
            # kapatılıp tazesi açılır (dataprep/vlm.py ile AYNI politika).
            try:
                from llm.providers.vllm_provider import reset_http_pool
                reset_http_pool(f"indeks/{type(exc).__name__}")
            except Exception:
                pass
            tunnel.refresh_if_needed()
            log.warning("    [İNDEKS RETRY] %s: %s — %.0fs sonra tekrar (%d)",
                        ne, type(exc).__name__, delay, deneme)
            time.sleep(delay)
            delay = min(delay * 2, _INDEKS_BACKOFF_MAX)


def drop_table_index(table_id: str) -> None:
    """Silinen bir tabloyu arama havuzundan da kaldırır.

    store.delete_table yalnız dosyayı ve registry kaydını siliyor; Qdrant'ta
    kalan kayıt mükerrerlik aramasını kirletiyordu (canlı 2026-08-20: dedup
    58 tabloyu birleştirdi, 58 "hayalet" indekste kaldı ve ajan artık var
    olmayan tablolarla eşleşme aradı)."""
    _, client = _shared()
    _dayanikli(lambda: client.delete(
        collection_name=TABLES_COLLECTION,
        points_selector=models.PointIdsList(points=[_table_point_id(table_id)])),
        f"drop({table_id[:32]})")


def index_table(table_id: str, topic: str, category: str, subcategory: str, docstring: str) -> None:
    """Yeni (ya da güncellenmiş) bir tabloyu Qdrant'a KALICI olarak yazar —
    search_tables bunu okur. create_table sonrası pipeline tarafından çağrılır.

    `topic` metnin EN BAŞINA ve tekrarlı konur: docstring'ler kalıplaşmış/şablon
    ağırlıklı olduğu için (ör. onlarca sigorta tablosu neredeyse birebir aynı
    cümleyle başlıyor), ayırt edici asıl bilgi (ürünün adı/türü) kalabalık ortak
    kelimeler arasında boğulup embedding benzerliğini bulanıklaştırıyordu
    (kanıtlı: "konut sigortası" araması gerçek 'konut-sigortası' tablosunu ilk
    5'e bile sokmadı). Konuyu öne çıkarmak ayırt ediciliği güçlendirir."""
    _dayanikli(_ensure_tables_collection, "koleksiyon")
    text = f"{topic}. {topic}. {category} {subcategory}: {docstring}"

    def _embed():
        # embedder HER DENEMEDE yeniden alınır: tünel değiştiğinde önbellek
        # anahtarı (model, URL) değiştiği için TAZE bir istemci döner. Bir kez
        # alıp saklamak, retry'ın hep ölü adrese gitmesi demekti.
        emb, _ = _shared()                    # _lock DIŞINDA (iç içe kilit yok)
        with _lock:
            return _embed_query_safe(emb, text)

    vec = _dayanikli(_embed, f"embed({table_id[:32]})")
    _, client = _shared()
    _dayanikli(lambda: client.upsert(collection_name=TABLES_COLLECTION, points=[models.PointStruct(
        id=_table_point_id(table_id), vector=vec,
        payload={"id": table_id, "topic": topic, "category": category,
                 "subcategory": subcategory, "docstring": docstring,
                 "text": text,                      # embed edilen metnin AYNISI
                 "indexed_at": _now_iso()})]), f"upsert({table_id[:32]})")


class _TableSearchArgs(BaseModel):
    query: str = Field(description="Sayfanın konusunu betimleyen doğal dil sorgusu "
                        "— en anlamca yakın 5 tabloyu (benzerlik skoruyla) getirir. "
                        "AYNI query'yi next=false ile tekrar göndermek BİREBİR AYNI "
                        "5 sonucu döner (deterministik) — FARKLI bir query yaz, ya "
                        "da aynı query için SONRAKİ 5 sonucu görmek istiyorsan "
                        "next=true gönder.")
    intent: str = Field(description="Bu aramayla TAM OLARAK NEYİ bulmaya çalıştığını "
                        "kısa bir cümleyle belirt. Arama embedding tabanlıdır ve bu "
                        "niyet cümlesi arama kalitesini doğrudan etkiler.")
    next: bool = Field(default=False, description="True ise: bu AYNI query için bir "
                        "önceki çağrıda görmediğin SONRAKİ 5 sonucu getirir "
                        "(sayfalama). False (varsayılan) ise en baştan en alakalı "
                        "5 sonucu getirir.")
    useful: list[str] = Field(default_factory=list, description="OPSİYONEL: bir "
                        "ÖNCEKİ sonuçtan gerçekten kullanışlı bulduğun tablo "
                        "id'leri — bu aramayla AYNI ÇAĞRIDA işaretleyebilirsin.")
    not_useful: list[str] = Field(default_factory=list, description="OPSİYONEL: bir "
                        "ÖNCEKİ sonuçtan gereksiz/konu dışı bulduğun tablo id'leri "
                        "— aynı çağrıda işaretlenip hemen silinir.")


def make_table_search_tool(registry_loader, marked: set | None = None,
                             discarded: set | None = None) -> StructuredTool:
    """`registry_loader`: kullanılmıyor gibi görünse de havuz TAMAMEN boşsa (0
    tablo, Qdrant koleksiyonu henüz yok) hızlı bir "kesin yeni konu" cevabı
    vermek için kullanılır — sorgu maliyetinden kaçınmak için.
    `marked`/`discarded`: search_bank ile AYNI desen — üretilen tüm ajanlar bu
    şekilde çalışsın: ilk 5 aday gelir, model isterse next=true ile sonraki 5'i
    ister, bunu yaparken önceki adaylardan gereksiz olanları not_useful ile eler
    (history'de sadece işe yarayanlar kalır)."""
    marked = marked if marked is not None else set()
    discarded = discarded if discarded is not None else set()
    _offsets: dict[str, int] = {}

    def _run(query: str, intent: str = "", next: bool = False,
              useful: list[str] = (), not_useful: list[str] = ()) -> str:
        mark_note = _apply_mark(useful, not_useful, marked, discarded)
        if not registry_loader():
            return "Henüz hiç karşılaştırma tablosu yok — bu kesin olarak yeni bir konu."
        key = query.strip().lower()
        offset = (_offsets.get(key, 0) + 5) if next else 0
        _offsets[key] = offset
        hits = search_tables(query, intent=intent, limit=5, offset=offset)
        if not hits:
            body = "Sonuç yok."
        else:
            # `ui_url` KASITLI olarak yazılmıyor: bu araç bir tablonun VAR OLUP
            # OLMADIĞINA karar vermek için, kullanıcıya link vermek için değil —
            # çevrimdışı hattın kullanıcısı yok.
            body = "\n---\n".join(
                f"id={h['id']} [{h['category']}/{h['subcategory']}] "
                f"benzerlik={h['score']:.2f}\n{h['docstring']}"
                for h in hits)
        return f"{mark_note}\n\n{body}" if mark_note else body

    return StructuredTool.from_function(
        func=_run, name="search_tables", args_schema=_TableSearchArgs,
        description=("Mevcut karşılaştırma tablolarında ANLAM bazlı ara (embedding "
                     "benzerliği). Sayfanın konusunu betimleyen bir sorgu yaz, en "
                     "yakın 5 tabloyu (id, kategori/alt-kategori, açıklama, benzerlik "
                     "skoru) getirir. Havuz büyük olabilir, hepsi tek seferde "
                     "gösterilmez — bu yüzden bu araçla ara. Emin olamıyorsan next=true "
                     "ile SONRAKİ 5'i görebilir ya da FARKLI bir sorguyla tekrar "
                     "arayabilirsin. intent alanına ne aradığını yazman arama "
                     "isabetini gerçekten etkiler, formalite değildir."))
