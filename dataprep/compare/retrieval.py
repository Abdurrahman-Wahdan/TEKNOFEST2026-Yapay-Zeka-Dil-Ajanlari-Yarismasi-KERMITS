"""Banka-scoped retrieval tool — bir subagent'ın ELİNE VERİLEN tek araç.

search_bank(query, k): subagent kendi query'sini yazar, k'yı kendi seçer, yetmezse
farklı query'yle veya daha büyük k ile TEKRAR çağırabilir. Banka sabittir (closure) —
subagent kendi bankasının dışına çıkamaz, "sadece o bankanın sorumlusu" tasarımı.

Qdrant 'campaigns' koleksiyonunu, metadata.bank filtresiyle arar. Süresi geçmiş
kampanyalar (metadata.campaign_end damgalıysa) elenir — bu tarih KIYASI, metinden
regex'le veri çekme değil; zaten Gemma'nın çıkardığı yapılandırılmış tarihi
karşılaştırıyoruz.
"""
from __future__ import annotations

import os
import queue
import threading

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from qdrant_client import models

from corpus import dates
from embeddings import get_embedding
from vector_stores.client import get_qdrant_client

COLLECTION = os.environ.get("QDRANT_COLLECTION_CAMPAIGNS", "campaigns")

_embed = None
_client = None
_lock = threading.Lock()   # 10 subagent aynı anda ilk çağrıyı yapabilir; tekil yükleme
EMBED_TIMEOUT = float(os.environ.get("COMPARE_EMBED_TIMEOUT", "30"))


def _shared():
    global _embed, _client
    with _lock:
        if _embed is None:
            _embed = get_embedding()
        if _client is None:
            _client = get_qdrant_client()
    return _embed, _client


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


def make_bank_search_tool(bank: str, marked: set, discarded: set) -> StructuredTool:
    """Bu bankaya SABİTLENMİŞ bir arama tool'u üretir (subagent bankayı seçemez).
    `marked`/`discarded`: mark_tool ile PAYLAŞILAN kümeler — bu çağrıya gömülü
    useful/not_useful kararı da aynı yere yazılır. `_offsets`: aynı query'nin
    'next=true' ile derinleştirilmesi için sorgu başına ilerleme takibi (closure)."""
    _offsets: dict[str, int] = {}

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
            end = meta.get("campaign_end")
            if end and not dates.is_active(end):        # süresi geçmiş -> gösterme
                continue
            url = meta.get("source_url") or meta.get("pdf_url") or meta.get("source_page") or ""
            out.append({
                "point_id": str(h.id), "url": url, "type": meta.get("type", ""),
                "text": (p.get("page_content") or "").strip()[:600],
                "campaign_end": end,
            })
        if not out and not hits:
            body = ("Sonuç yok" + (" (bu query için artık daha fazla sonuç kalmadı, "
                     "next ile ilerleme sınırına ulaştın)" if next else
                     " (bu banka için bu sorguyla hiçbir güncel içerik bulunamadı)")
                     + ".")
        elif not out:
            body = "Bu gruptaki sonuçların hepsi süresi geçmiş kampanyaydı, gösterilmedi."
        else:
            body = "\n---\n".join(
                f"[{i+1}] point_id={o['point_id']} url={o['url']} campaign_end={o['campaign_end']}\n{o['text']}"
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


class _FullPageArgs(BaseModel):
    url: str = Field(description="search_bank sonucunda gördüğün 'url=' değeri.")


def make_full_page_tool(bank: str) -> StructuredTool:
    """search_bank sadece bir chunk (parça, ~600 karakter) döndürür — sayfanın
    KENDİSİ o parçada kesilmiş/eksik görünüyorsa, agent bu tool'la aynı URL'nin
    TÜM chunk'larını (chunk_index sırasıyla) birleştirilmiş, tam metin olarak
    okuyabilir. Banka sabit (closure) — sadece kendi bankasının sayfalarını okur."""

    def _run(url: str) -> str:
        _, client = _shared()
        url_match = models.Filter(should=[
            models.FieldCondition(key="metadata.source_url", match=models.MatchValue(value=url)),
            models.FieldCondition(key="metadata.pdf_url", match=models.MatchValue(value=url)),
            models.FieldCondition(key="metadata.source_page", match=models.MatchValue(value=url)),
            models.FieldCondition(key="metadata.gorsel_url", match=models.MatchValue(value=url)),
        ])
        flt = models.Filter(must=[
            models.FieldCondition(key="metadata.bank", match=models.MatchValue(value=bank)),
            url_match,
        ])
        points, _ = client.scroll(collection_name=COLLECTION, scroll_filter=flt,
                                   limit=200, with_payload=True)
        if not points:
            return "Bu URL için chunk bulunamadı (yanlış url ya da başka bankaya ait olabilir)."
        rows = sorted(
            ((p.payload or {}).get("metadata", {}).get("chunk_index", 0),
             (p.payload or {}).get("page_content", "")) for p in points)
        return "\n\n".join(t for _, t in rows if t.strip())

    return StructuredTool.from_function(
        func=_run, name="read_full_page", args_schema=_FullPageArgs,
        description=("search_bank bir sayfanın yalnızca KÜÇÜK BİR PARÇASINI (chunk) "
                     "getirir; kesilmiş/yarım/yetersiz görünüyorsa bu tool'la aynı "
                     "sonucun 'url' değerini vererek o sayfanın/PDF'in TÜM parçalarını "
                     "birleştirilmiş, TAM metin olarak okuyabilirsin."))


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


def index_table(table_id: str, topic: str, category: str, subcategory: str, docstring: str) -> None:
    """Yeni (ya da güncellenmiş) bir tabloyu Qdrant'a KALICI olarak yazar —
    search_tables bunu okur. create_table sonrası pipeline tarafından çağrılır.

    `topic` metnin EN BAŞINA ve tekrarlı konur: docstring'ler kalıplaşmış/şablon
    ağırlıklı olduğu için (ör. onlarca sigorta tablosu neredeyse birebir aynı
    cümleyle başlıyor), ayırt edici asıl bilgi (ürünün adı/türü) kalabalık ortak
    kelimeler arasında boğulup embedding benzerliğini bulanıklaştırıyordu
    (kanıtlı: "konut sigortası" araması gerçek 'konut-sigortası' tablosunu ilk
    5'e bile sokmadı). Konuyu öne çıkarmak ayırt ediciliği güçlendirir."""
    _ensure_tables_collection()
    embedder, client = _shared()
    text = f"{topic}. {topic}. {category} {subcategory}: {docstring}"
    with _lock:
        vec = _embed_query_safe(embedder, text)
    client.upsert(collection_name=TABLES_COLLECTION, points=[models.PointStruct(
        id=_table_point_id(table_id), vector=vec,
        payload={"id": table_id, "category": category, "subcategory": subcategory,
                 "docstring": docstring})])


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
        _ensure_tables_collection()
        embedder, client = _shared()
        key = query.strip().lower()
        offset = (_offsets.get(key, 0) + 5) if next else 0
        _offsets[key] = offset
        # `intent`: statik/sabit bir talimat metni BİZ yazmıyoruz — modelin kendi
        # ifade ettiği arama niyeti kullanılıyor (asimetrik retrieval talimatı).
        with _lock:
            qvec = _embed_query_safe(embedder, query, task=intent or None)
        hits = client.query_points(collection_name=TABLES_COLLECTION, query=qvec,
                                    limit=5, offset=offset, with_payload=True).points
        if not hits:
            body = "Sonuç yok."
        else:
            body = "\n---\n".join(
                f"id={h.payload['id']} [{h.payload.get('category', '')}/"
                f"{h.payload.get('subcategory', '')}] benzerlik={h.score:.2f}\n"
                f"{h.payload['docstring']}"
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
