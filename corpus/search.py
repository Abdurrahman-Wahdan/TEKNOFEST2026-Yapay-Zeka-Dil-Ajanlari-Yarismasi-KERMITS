"""Banka-scoped retrieval — bir ajanın ELİNE VERİLEN corpus araçları.

Üç araç, hepsinde banka SABİT (closure), hiçbirinde `bank` argümanı YOK:

  search_bank(query, intent, next, useful, not_useful)
      Ajan kendi sorgusunu yazar, yetmezse FARKLI bir sorguyla ya da aynı sorgu
      için next=true ile TEKRAR çağırır. Her çağrı 5 sonuç.
  expand_chunk(point_id, before, after)
      Bir sonuç ORTASINDAN KESİLMİŞ görünüyorsa komşu parçaları getirir.
  read_full_page(url)
      Elde sadece bir URL varken (ör. bir atıfta görülen) o belgenin tamamı.

Neden üçü birden: `dataprep/embed.py` metni 9000 karakterlik parçalara ÜST ÜSTE
BİNME OLMADAN (OVERLAP=0) böler. Ölçüldü: 5613 belgenin 287'si (%5) çok parçalı
ve koleksiyonun %18'i (1184 parça) o belgelerde. Yani bir arama sonucu cümlenin
ya da tablonun ortasında bitebiliyor ve devamı KOMŞU parçada. En büyük belge 55
parça / 454.981 karakter — tamamını döndürmek modelin 131k'lik penceresinden
büyük, o yüzden "kesilmiş" sorusunun doğru cevabı read_full_page değil,
expand_chunk ile adım adım genişletmek.

Qdrant 'campaigns' koleksiyonunu metadata.bank filtresiyle arar. Süresi geçmiş
kampanyalar ARAMADA elenir — bu tarih KIYASI, metinden regex'le veri çekme
değil; zaten çıkarım hattının yazdığı yapılandırılmış tarihi karşılaştırıyoruz.

Bu modül hem canlı banka uzmanlarının (agents/shared) hem de çevrimdışı
karşılaştırma hattının (dataprep/compare) ortak kaynağıdır: aynı Türkçe metin,
aynı süresi-geçmiş kuralı, aynı metadata okuması TEK yerde tanımlı.
"""
from __future__ import annotations

import contextlib
import os
import re
import queue
import threading

from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from qdrant_client import models

from config.settings import settings

from . import dates

COLLECTION = os.environ.get("QDRANT_COLLECTION_CAMPAIGNS", "campaigns")

RESULTS_PER_CALL = 5   # sabit grup boyutu — model sayı seçmez

# Aynı sorguyu next=true ile kaç kez derinleştirebilir. Sınırsız derinlik azalan
# getiridir: kanıtlı olarak bir konuda 42 kez art arda derinleşip hiç durmadı.
NEXT_DEPTH_LIMIT = int(os.environ.get("CORPUS_NEXT_DEPTH_LIMIT", "4"))
# HARFİYEN aynı sorgunun kaç kez tekrarlanabileceği. Arama deterministik: aynı
# sorgu next=false ile birebir aynı 5 sonucu döner, tekrarı ilerleme değildir.
REPEAT_LIMIT = int(os.environ.get("CORPUS_REPEAT_LIMIT", "2"))

# Tek bir araç cevabının döndürebileceği en fazla karakter. Bu bir KIRPMA EŞİĞİ
# DEĞİL: aşıldığında içeriğin bir kısmı değil, HİÇBİRİ dönmez — bunun yerine
# belgenin boyutu ve parça aralığı bildirilir, ajan daha dar bir aralık ister.
# Modelden hiçbir şey saklanmaz; sadece pencereye sığmayacak bir yük sessizce
# gönderilmez. Varsayılan 60.000 ≈ ölçülen p90 parça boyutunda (7.457) 8 parça.
MAX_TOOL_CHARS = int(os.environ.get("CORPUS_MAX_TOOL_CHARS", "60000"))

EMBED_TIMEOUT = float(os.environ.get("COMPARE_EMBED_TIMEOUT", "30"))

_gpu_lock = threading.Lock()    # bkz. _embed_lock


def _shared():
    """Embedder ve Qdrant istemcisi.

    BURADA CACHE YOK, bilerek: ikisini de kendi fabrikaları zaten süreç boyunca
    cache'liyor (embeddings/providers, vector_stores/client) VE ikisinin de o
    cache'i geçersiz kılmanın bir yolu var — tünel URL'si döndüğünde, ya da
    testler arasında. Buraya üçüncü bir cache koymak o geçersiz kılmayı atlar:
    kapatılmış bir istemciyi ya da eski bir URL'ye bağlı bir embedder'ı süreç
    ölene kadar elde tutar (kanıtlı: cache'ler temizlendikten sonra her arama
    "Cannot send a request, as the client has been closed" ile düşüyordu).
    """
    from embeddings import get_embedding
    from vector_stores.client import get_qdrant_client

    return get_embedding(), get_qdrant_client()


def _embed_lock():
    """Embedding çağrılarını SADECE gerçekten gerektiği yerde serileştirir.

    PyTorch MPS (Apple GPU) eşzamanlı thread'lerden çağrılınca sessizce çöküyor
    ya da donuyor — bu YEREL sağlayıcının sorunu. Varsayılan sağlayıcı artık
    'remote' (embedding modeli sohbet modelleriyle aynı sunucuda), orada böyle
    bir kısıt yok ve kilidi orada da uygulamak canlı istek yolunda ON uzmanın
    aramasını sıraya dizerdi.
    """
    if settings.EMBEDDING_PROVIDER == "local":
        return _gpu_lock
    return contextlib.nullcontext()


def _embed_query_safe(embedder, query: str, task: str | None = None):
    """embed_query'yi SABİT SÜRELİ bir duvarla çağırır. Yerel GPU donarsa (kanıtlı:
    869 saniye CPU %0, hiçbir log yok) çağrı sonsuza kadar beklerdi. Ayrı bir
    daemon thread'de çalıştırıp EMBED_TIMEOUT'ta sonuç gelmezse hata fırlatırız;
    donan thread yalnız kendi başına (daemon) sürer, süreci bloklamaz.

    `task`: SADECE gerçek arama SORGULARI için verilir (indekslenen belge metni
    için değil) — Qwen3-Embedding asimetrik retrieval'da sorgu tarafına bir görev
    talimatı öneki bekliyor, yoksa (kanıtlı) ayırt edici olmayan skorlar üretiyor.
    """
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


def embed_query(query: str, task: str | None = None):
    """Tek bir sorguyu vektöre çevirir — kilit ve zaman aşımı dahil."""
    embedder, _ = _shared()
    with _embed_lock():
        return _embed_query_safe(embedder, query, task=task)


# --- payload metadata: İKİ ayrı isimlendirme kuşağı -------------------------
# Koleksiyondaki 6510 point'in TAMAMI şu anahtarları taşıyor: bank, url, type
# (metin/gorsel), chunk_index, validity_status + tarih varsa gecerlilik_baslangic/
# gecerlilik_bitis. Bir önceki kuşağın isimleri (source_url, pdf_url, source_page,
# campaign_end, campaign_status) ise 6510'un SIFIRINDA var — yani sadece o
# isimlere bakan kod, canlı koleksiyona karşı her sonuçta boş url döndürür ve
# süresi geçmiş kampanya filtresi hiç çalışmaz.
#
# İkisi de okunuyor: yeni isim önce, eski isim yedek. Böylece bu araçlar hem şu
# anki koleksiyona hem de eski bir indekse karşı doğru çalışır.
def _source_url(meta: dict) -> str:
    return (meta.get("url") or meta.get("source_url") or meta.get("pdf_url")
            or meta.get("source_page") or "")


def _end_date(meta: dict) -> str:
    """Kampanyanın bitiş tarihi (ISO). Yoksa boş — tarihsiz olmak 'bitmiş' demek
    değil, sadece sayfanın tarih yazmamış olması demek."""
    return meta.get("gecerlilik_bitis") or meta.get("campaign_end") or ""


def _expired(meta: dict, end: str) -> bool:
    """Süresi geçmiş mi. Tarih varsa BUGÜNE karşı hesaplanır (damganın kendisine
    değil): damga tarama anında yazıldı, tarih ise sonrasında da doğru kalıyor.
    Tarih yoksa üreticinin kendi hükmüne bakılır — yeni kuşakta
    validity_status='suresi_gecmis', eskisinde campaign_status='bitti'."""
    if end:
        return not dates.is_active(end)
    return (meta.get("validity_status") == "suresi_gecmis"
            or meta.get("campaign_status") == "bitti")


# --- işaretleme / budama ----------------------------------------------------

# Sonuçlar bir AYIRAÇ metniyle değil, kendi BAŞLIK satırlarıyla ayrılır.
#
# Önceki biçim parçaları "\n---\n" ile ayırıyordu ve bu, corpus metninin kendi
# içinde bulunan bir şey: markdown yatay çizgisi. Ölçüldü: 6510 parçanın 138'i
# (%2,1) tam olarak bu diziyi içeriyor. Ayıraçla bölünce o parçalar İKİYE
# bölünüyor, budama yalnız point_id'yi taşıyan yarıyı tanıyor ve diğer yarısı
# bağlamda sahipsiz bir metin kırıntısı olarak kalıyordu (ya da atılan bir
# parçanın kuyruğu, atıfsız biçimde, hayatta kalıyordu).
#
# Başlık satırının biçimi BİZE ait, gövde ise bankaya: bu yüzden ayrım gövdenin
# içeriğinden bağımsız olmalı. `url=` de aranıyor, çünkü tek başına
# "[1] point_id=" bir belgede tesadüfen geçebilir.
_ENTRY_HEAD = re.compile(r"^\[\d+\] point_id=(\S+) url=", re.M)
_ENTRY_SEP = "\n\n"

#: Çıktısı parça bazında budanabilen araçlar. Budama SADECE bunlara uygulanır:
#: canlı uç nokta cevapları (finance_quote vb.) ya da sohbetin kendisi bu yoldan
#: ASLA değiştirilemez, içinde ne yazıyor olursa olsun.
PRUNABLE_TOOLS = frozenset({"search_bank", "expand_chunk", "read_full_page"})


def _apply_mark(useful, not_useful, marked: set, discarded: set) -> str:
    """useful/not_useful kararını uygular. SON karar geçerli: adım sayısı arttıkça
    model önce 'kalsın' dediğine sonra 'silinsin' diyebilir (ya da tersi).

    BİLİNÇLİ OLARAK bağımsız bir mark_useful aracı YOK. İşaretlemenin tek işlevi
    bir parçayı gelecekteki budamadan korumak ya da hemen sildirmek; bağımsız bir
    araç olduğunda model onu tek başına, ilerleme kaydetmeden art arda çağırıp
    döngüye giriyordu (kanıtlı bug, 50 çağrılık örnek). O yüzden işaretleme
    yalnızca İÇERİK GETİREN çağrıların alanlarına gömülü.
    """
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
        parts.append(f"{len(new_discard)} sonuç gereksiz işaretlendi (bağlamdan "
                     "silindi, bir daha görmeyeceksin).")
    return " ".join(parts)


def prune_entries(content: str, discarded: set[str]) -> str | None:
    """Bir araç cevabından, modelin 'gereksiz' dediği parçaları çıkarır.

    Cevap birden çok parça içerir ([1] point_id=..., [2] point_id=...); model her
    parça için AYRI karar verir. Burada yalnızca o parçalar çıkarılır, kalanlara
    ve cevabın geri kalanına dokunulmaz. Değişiklik yoksa None döner.

    Bölme BAŞLIK satırlarına göre yapılır, gövdenin içine bakılmadan: gövde
    banka metnidir ve içinde her şey olabilir (bkz. _ENTRY_HEAD'in üstündeki
    not). İlk başlıktan ÖNCEKİ her şey -- işaretleme notu, belge özeti -- her
    zaman korunur; o bir parça değil, cevabın çerçevesi.
    """
    if not discarded:
        return None
    heads = list(_ENTRY_HEAD.finditer(content))
    if not heads:
        return None
    prefix = content[:heads[0].start()]
    kept, dropped = [], 0
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(content)
        if head.group(1) in discarded:
            dropped += 1
        else:
            kept.append(content[head.start():end].rstrip())
    if not dropped:
        return None
    body = _ENTRY_SEP.join(kept) if kept else "(model bu grubu gereksiz bulup sildi)"
    return f"{prefix}{body}" if prefix.strip() else body


def prune_messages(messages: list, discarded: set[str]) -> list:
    """Budanmış ToolMessage'ları döndürür (değişmeyenler listeye girmez).

    `tool_name_of` ile SADECE retrieval araçlarının cevapları budanır; bir
    finance_quote zarfı bu yoldan değiştirilemez.
    """
    out = []
    for i, m in enumerate(messages):
        if not isinstance(m, ToolMessage):
            continue
        if tool_name_of(m, messages[:i]) not in PRUNABLE_TOOLS:
            continue
        content = m.content if isinstance(m.content, str) else str(m.content)
        pruned = prune_entries(content, discarded)
        if pruned is not None:
            out.append(m.model_copy(update={"content": pruned}))
    return out


def tool_name_of(message: ToolMessage, earlier: list) -> str:
    """Bir ToolMessage'ı hangi aracın ürettiği. `name` boş olabilir; o zaman
    çağrıyı yapan AIMessage'ın tool_call'una bakılır (LangChain'in kendi
    ClearToolUsesEdit'inin yaptığı arama)."""
    if message.name:
        return message.name
    for earlier_message in reversed(earlier):
        for call in getattr(earlier_message, "tool_calls", None) or ():
            if call.get("id") == message.tool_call_id:
                return call.get("name") or ""
    return ""


# --- ortak sorgulama --------------------------------------------------------

def _bank_filter(bank: str, *extra) -> models.Filter:
    """Her sorgunun taşıdığı KOŞUL: metadata.bank bu banka. Qdrant bunu skorlama
    ÖNCESİNDE uygular — başka bir bankanın parçası aday bile olmaz, dolayısıyla
    hiçbir sırada görünemez. Banka closure'da; araçların hiçbirinde `bank`
    argümanı yok, yani model başka bir bankayı isteyecek bir alan bulamaz."""
    return models.Filter(must=[
        models.FieldCondition(key="metadata.bank", match=models.MatchValue(value=bank)),
        *extra,
    ])


def _doc_spans(client, bank: str, keys: set[tuple[str, str]]) -> dict[tuple[str, str], int]:
    """(url, type) -> o belgedeki parça sayısı. TEK sorguda, sadece chunk_index
    payload'ı çekilerek: amaç bir sonucun kesilmiş OLABİLECEĞİNİ ajana söylemek,
    metni buraya taşımak değil."""
    keys = {k for k in keys if k[0]}
    if not keys:
        return {}
    flt = _bank_filter(bank, models.Filter(should=[
        models.Filter(must=[
            models.FieldCondition(key="metadata.url", match=models.MatchValue(value=url)),
            models.FieldCondition(key="metadata.type", match=models.MatchValue(value=kind)),
        ]) for url, kind in keys
    ]))
    spans: dict[tuple[str, str], int] = {}
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=COLLECTION, scroll_filter=flt, limit=500, offset=offset,
            with_payload=["metadata.url", "metadata.type"])
        for p in points:
            meta = (p.payload or {}).get("metadata", {}) or {}
            key = (meta.get("url", ""), meta.get("type", ""))
            spans[key] = spans.get(key, 0) + 1
        if offset is None:
            return spans


def _entry(index: int, point_id, meta: dict, text: str, total: int | None = None) -> str:
    """Bir sonuç parçası, HER araçta aynı biçimde: ilk satır künye, altı tam metin.

    Biçimin aynı olması şart — budama (prune_entries) bu biçime göre çalışıyor,
    yani expand_chunk ile getirilen bir parça da search_bank sonucu gibi
    not_useful ile atılabiliyor. İkinci bir yol yok.
    """
    head = [f"[{index}] point_id={point_id}", f"url={_source_url(meta)}"]
    end = _end_date(meta)
    if end:
        head.append(f"gecerlilik_bitis={end}")
    if _expired(meta, end):
        head.append("DURUM=SÜRESİ_GEÇMİŞ")
    if total is not None and total > 1:
        # Sadece ÇOK PARÇALI belgelerde yazılır. Tek parçalı belgelerde (ölçüldü:
        # belgelerin %95'i) parça numarası gürültüdür ve "kesilmiş olabilir mi"
        # sorusunu akla getirmesi de istenmez — o belge zaten tam.
        index = int(meta.get("chunk_index", 0) or 0)
        head.append(f"parça={index}/{total - 1}")
        if index < total - 1 and _ends_cut(text):
            head.append("KESİK=devamı_sonraki_parçada")
    return " ".join(head) + "\n" + text


def _ends_cut(text: str) -> bool:
    """Bu parça, cümlesi/bölümü BİTMEDEN mi kesiliyor.

    Sistem talimatına güvenmek yetmedi: canlı denemede model, yalnızca bir
    BAŞLIKLA biten bir parçayı gördü ("## 15. ... Kullandırılması", altında tek
    satır yok) ve bunu "banka böyle bir madde yayımlamamış" diye yorumlayıp
    başka belgelerden cevap üretti. Başlık kendi içinde tamdır — kesik görünmez.
    Kesik olan, başlığın ADLANDIRDIĞI bölümdür ve o bir sonraki parçadadır.

    O yüzden uyarı, sistem talimatında değil, KANITIN YANINDA duruyor: bu bilgi
    aracın elinde zaten var, modelin çıkarmasını beklemek yerine söylüyoruz.
    """
    line = next((ln.strip() for ln in reversed(text.strip().splitlines()) if ln.strip()), "")
    if not line:
        return False
    # Bir başlıkla bitiyor: adlandırdığı bölüm henüz başlamamış.
    if line.startswith("#"):
        return True
    # Tamamlanmamış bir tablo satırı ya da bitmemiş bir cümle.
    return not line.endswith((".", "!", "?", ":", ";", ")", "»", '"'))


def _too_large(chars: int, count: int, hint: str) -> str:
    return (f"Bu istek {count} parça / {chars} karakter tutuyor ve tek bir araç "
            f"cevabı için fazla ({MAX_TOOL_CHARS} karakter sınırı). Hiçbir şey "
            f"kırpılmadı — daha dar bir aralık iste. {hint}")


# --- search_bank ------------------------------------------------------------

class _SearchArgs(BaseModel):
    query: str = Field(description="Arama sorgusu — doğal dil, TÜRKÇE (aranan "
                       "içeriğin tamamı Türkçe). Her çağrı en alakalı 5 sonucu "
                       "getirir. AYNI query'yi next=false ile tekrar göndermek "
                       "BİREBİR AYNI 5 sonucu döner (deterministik, yenilik "
                       "gelmez) — bunun yerine ya FARKLI bir query yaz, ya da aynı "
                       "query için SONRAKİ 5 sonucu görmek istiyorsan next=true "
                       "gönder.")
    intent: str = Field(description="Bu aramayla TAM OLARAK NEYİ bulmaya çalıştığını "
                        "kısa bir cümleyle belirt. Arama embedding tabanlıdır ve bu "
                        "niyet cümlesi arama kalitesini doğrudan etkiler — boş "
                        "bırakma, her çağrıda güncelle.")
    next: bool = Field(default=False, description="True ise: bu AYNI query için bir "
                       "önceki çağrıda görmediğin SONRAKİ 5 sonucu getirir (aynı "
                       "sorgunun derinleştirilmesi — sayfalama). False (varsayılan) "
                       "ise en baştan en alakalı 5 sonucu getirir.")
    useful: list[str] = Field(default_factory=list, description="OPSİYONEL: bir "
                              "ÖNCEKİ sonuçtan gerçekten kullanışlı bulduğun "
                              "point_id'ler — bu aramayla AYNI ÇAĞRIDA "
                              "işaretlersin, ayrı bir adım harcamana gerek yok.")
    not_useful: list[str] = Field(default_factory=list, description="OPSİYONEL: bir "
                                  "ÖNCEKİ sonuçtan gereksiz/konu dışı bulduğun "
                                  "point_id'ler — bağlamdan hemen silinir, bir daha "
                                  "görmezsin. Uzun araştırmalarda BUNU KULLAN.")


def make_bank_search_tool(bank: str, marked: set, discarded: set) -> StructuredTool:
    """Bu bankaya SABİTLENMİŞ bir arama aracı üretir (ajan bankayı seçemez).

    `marked`/`discarded`: çağrıya gömülü useful/not_useful kararının yazıldığı,
    budamayla PAYLAŞILAN kümeler.

    Sayaçlar closure'da ve kilitli: LangGraph aynı turdaki tool çağrılarını
    eşzamanlı yürütüyor, ajan aynı anda birden çok arama yapabilir.
    """
    state_lock = threading.Lock()
    offsets: dict[str, int] = {}
    depth: dict[str, int] = {}
    repeats: dict[str, int] = {}

    def _run(query: str, intent: str = "", useful: list[str] = (),
             not_useful: list[str] = (), next: bool = False) -> str:
        mark_note = _apply_mark(useful, not_useful, marked, discarded)
        key = query.strip().lower()

        # Fiziksel tavan, İÇERİK kararı değil: aynı sorguda ısrar ilerleme
        # sayılmaz. Çevrimdışı hatta bu kontrol elle yazılmış döngüdeydi;
        # create_agent'ta öyle bir döngü yok, o yüzden aracın kendi içinde.
        with state_lock:
            if next:
                n = depth[key] = depth.get(key, 0) + 1
                exhausted = n > NEXT_DEPTH_LIMIT
                offset = offsets.get(key, 0) + RESULTS_PER_CALL
            else:
                n = repeats[key] = repeats.get(key, 0) + 1
                exhausted = n > REPEAT_LIMIT
                offset = 0
            if not exhausted:
                offsets[key] = offset
        if exhausted:
            body = ("Bu sorguyu yeterince derinleştirdin, daha fazlası yeni bilgi "
                    "getirmiyor. FARKLI bir sorgu yaz ya da topladığın bilgiyle "
                    "cevabını ver." if next else
                    "Bu sorguyu zaten aynen denedin; arama deterministik olduğu "
                    "için sonuç değişmez. FARKLI bir sorgu yaz, aynı sorguda "
                    "derinleşmek istiyorsan next=true kullan, ya da cevabını ver.")
            return f"{mark_note}\n\n{body}" if mark_note else body

        vector = embed_query(query, task=intent or None)
        _, client = _shared()
        hits = client.query_points(
            collection_name=COLLECTION, query=vector, query_filter=_bank_filter(bank),
            limit=RESULTS_PER_CALL, offset=offset, with_payload=True).points

        kept = []
        for h in hits:
            payload = h.payload or {}
            meta = payload.get("metadata", {}) or {}
            if _expired(meta, _end_date(meta)):     # süresi geçmiş -> gösterme
                continue
            kept.append((h, meta, (payload.get("page_content") or "").strip()))

        spans = _doc_spans(client, bank, {(_source_url(m), m.get("type", ""))
                                          for _, m, _ in kept})
        if not kept and not hits:
            body = ("Sonuç yok" + (" (bu sorgu için artık daha fazla sonuç kalmadı)"
                    if next else " (bu banka için bu sorguyla hiçbir güncel içerik "
                    "bulunamadı)") + ".")
        elif not kept:
            body = "Bu gruptaki sonuçların hepsi süresi geçmiş kampanyaydı, gösterilmedi."
        else:
            body = _ENTRY_SEP.join(
                _entry(i + 1, h.id, meta, text,
                       spans.get((_source_url(meta), meta.get("type", ""))))
                for i, (h, meta, text) in enumerate(kept))
        return f"{mark_note}\n\n{body}" if mark_note else body

    return StructuredTool.from_function(
        func=_run, name="search_bank", args_schema=_SearchArgs,
        description=("Bu bankanın KENDİ yayımladığı sayfalarda ve PDF'lerde ara "
                     "(yalnız bu bankada, başkasında değil). Her çağrı 5 sonuç "
                     "getirir — kaç sonuç istediğine SEN karar vermezsin. AYNI "
                     "query'yi next=false ile tekrar göndermek BİREBİR AYNI 5 "
                     "sonucu döner (deterministik). Aynı konuda SONRAKİ 5 sonucu "
                     "görmek istiyorsan query'yi DEĞİŞTİRMEDEN next=true gönder; "
                     "farklı bir konuya geçiyorsan yeni bir query yaz. Süresi "
                     "geçmiş kampanyalar zaten gösterilmez. Bir sonuçta 'parça=' "
                     "yazıyorsa o belge çok parçalıdır ve metin kesilmiş olabilir "
                     "— devamı için expand_chunk kullan. Önceki sonuçlar için "
                     "useful/not_useful kararını BU çağrıya göm. Arama embedding "
                     "(anlam vektörü) tabanlıdır — intent alanına ne aradığını "
                     "yazman arama isabetini gerçekten etkiler, formalite değil."))


# --- expand_chunk -----------------------------------------------------------

class _ExpandArgs(BaseModel):
    point_id: str = Field(description="Genişletmek istediğin sonucun point_id "
                          "değeri (search_bank ya da expand_chunk çıktısında "
                          "'point_id=' olarak görünür).")
    before: int = Field(default=1, ge=0, le=50, description="Bu parçadan ÖNCE kaç "
                        "parça getirilsin. Metnin BAŞI kesikse artır.")
    after: int = Field(default=1, ge=0, le=50, description="Bu parçadan SONRA kaç "
                       "parça getirilsin. Metnin SONU kesikse artır.")
    useful: list[str] = Field(default_factory=list, description="OPSİYONEL: kalıcı "
                              "olarak korunmasını istediğin point_id'ler.")
    not_useful: list[str] = Field(default_factory=list, description="OPSİYONEL: "
                                  "bağlamdan silinmesini istediğin point_id'ler.")


def make_expand_chunk_tool(bank: str, marked: set, discarded: set) -> StructuredTool:
    """Bir parçanın KOMŞULARINI getiren, bu bankaya sabitlenmiş araç.

    Parçalar üst üste binmeden (OVERLAP=0) bölündüğü için bir sonuç cümlenin ya
    da tablonun ortasında bitebiliyor. Ajan buradan adım adım genişleyerek
    okuduğu pasajı tamamlar; belgenin tamamını (bazen 455.000 karakter) bağlama
    taşımak zorunda kalmaz.
    """

    def _run(point_id: str, before: int = 1, after: int = 1,
             useful: list[str] = (), not_useful: list[str] = ()) -> str:
        mark_note = _apply_mark(useful, not_useful, marked, discarded)

        def answer(body: str) -> str:
            return f"{mark_note}\n\n{body}" if mark_note else body

        _, client = _shared()
        # Çapa parçası, banka koşuluyla BİRLİKTE aranır: başka bir bankanın
        # point_id'si -- nereden gelmiş olursa olsun -- hiçbir şeye çözülmez.
        anchor, _ = client.scroll(
            collection_name=COLLECTION, limit=1, with_payload=True,
            scroll_filter=_bank_filter(bank, models.HasIdCondition(has_id=[point_id])))
        if not anchor:
            return answer("Bu point_id bu bankada bulunamadı (yanlış id olabilir, "
                          "ya da başka bir bankaya ait).")

        meta = (anchor[0].payload or {}).get("metadata", {}) or {}
        url, kind = _source_url(meta), meta.get("type", "")
        index = int(meta.get("chunk_index", 0) or 0)
        # `type` de filtreye giriyor: bir kampanya sayfasının görselleri sayfanın
        # URL'sini PAYLAŞIYOR ve onların chunk_index'i metin parçalarını değil
        # görselleri sayıyor (bir Ziraat sayfasında 28 tane). İkisi karışırsa bir
        # görsel açıklaması cümlenin ortasına giriyor.
        same_doc = [
            models.FieldCondition(key="metadata.url", match=models.MatchValue(value=url)),
            models.FieldCondition(key="metadata.type", match=models.MatchValue(value=kind)),
        ]
        span = _bank_filter(bank, *same_doc, models.FieldCondition(
            key="metadata.chunk_index",
            range=models.Range(gte=index - before, lte=index + after)))
        points, _ = client.scroll(collection_name=COLLECTION, scroll_filter=span,
                                  limit=200, with_payload=True)
        total = _doc_spans(client, bank, {(url, kind)}).get((url, kind), len(points))

        rows = sorted(
            (int(((p.payload or {}).get("metadata", {}) or {}).get("chunk_index", 0) or 0),
             p.id,
             (p.payload or {}).get("metadata", {}) or {},
             ((p.payload or {}).get("page_content") or "").strip())
            for p in points)
        chars = sum(len(text) for _, _, _, text in rows)
        last = total - 1
        if chars > MAX_TOOL_CHARS:
            return answer(_too_large(
                chars, len(rows),
                f"Bu belgede 0..{last} arası parça var; sen {index - before}.."
                f"{index + after} aralığını istedin."))

        body = _ENTRY_SEP.join(
            _entry(i + 1, pid, m, text, total)
            for i, (_, pid, m, text) in enumerate(rows))
        first_index = rows[0][0] if rows else index
        last_index = rows[-1][0] if rows else index
        # Özet, parçaların ÖNÜNDE: budama ilk başlıktan öncesine dokunmaz, yani
        # ajan gördüğü parçaların hepsini atsa bile belgenin şeklini kaybetmez.
        summary = (f"Bu belgede 0..{last} arası parça var; şu an {first_index}.."
                   f"{last_index} arasını görüyorsun.")
        summary += (" Daha yukarısı/aşağısı için before/after değerlerini artır."
                    if (first_index > 0 or last_index < last) else
                    " Belgenin tamamını görüyorsun.")
        return answer(f"{summary}\n\n{body}")

    return StructuredTool.from_function(
        func=_run, name="expand_chunk", args_schema=_ExpandArgs,
        description=("Bir arama sonucu CÜMLENİN ya da TABLONUN ORTASINDA kesilmiş "
                     "görünüyorsa, o sonucun point_id'siyle KOMŞU parçalarını "
                     "getirir — devamı (ve öncesi) komşu parçadadır, çünkü belgeler "
                     "üst üste binmeden bölünür. Eksik kalan yeri TAHMİN ETME, bu "
                     "araçla oku. Cevabın sonunda belgede toplam kaç parça olduğu "
                     "ve şu an hangilerini gördüğün yazar; yetmezse before/after "
                     "değerlerini artırıp tekrar çağır, pasaj tamamlanana kadar "
                     "adım adım genişleyebilirsin. Yalnız bu bankanın parçalarını "
                     "açar."))


# --- read_full_page ---------------------------------------------------------

class _FullPageArgs(BaseModel):
    url: str = Field(description="Bir sonuçta gördüğün 'url=' değeri.")
    useful: list[str] = Field(default_factory=list, description="OPSİYONEL: kalıcı "
                              "olarak korunmasını istediğin point_id'ler.")
    not_useful: list[str] = Field(default_factory=list, description="OPSİYONEL: "
                                  "bağlamdan silinmesini istediğin point_id'ler.")


def make_full_page_tool(bank: str, marked: set | None = None,
                        discarded: set | None = None) -> StructuredTool:
    """Bir URL'nin TÜM parçalarını (chunk_index sırasıyla) getirir. Banka sabit.

    Elde point_id değil sadece bir URL varken kullanılır. Bir sonucun devamını
    okumak için DOĞRU araç bu değil, expand_chunk'tır: en büyük belge 55 parça /
    454.981 karakter ve tamamı modelin penceresinden büyük.
    """
    marked = marked if marked is not None else set()
    discarded = discarded if discarded is not None else set()

    def _run(url: str, useful: list[str] = (), not_useful: list[str] = ()) -> str:
        mark_note = _apply_mark(useful, not_useful, marked, discarded)

        def answer(body: str) -> str:
            return f"{mark_note}\n\n{body}" if mark_note else body

        _, client = _shared()
        # Canlı koleksiyonda URL'yi taşıyan anahtar metadata.url; görsellerin
        # kaynağı ayrıca metadata.gorsel_kaynak'ta. Bir önceki kuşağın isimleri
        # (source_url/pdf_url/source_page/gorsel_url) yedek olarak duruyor —
        # bkz. _source_url'ün üstündeki not.
        url_match = models.Filter(should=[
            models.FieldCondition(key=f"metadata.{key}",
                                  match=models.MatchValue(value=url))
            for key in ("url", "gorsel_kaynak", "source_url", "pdf_url",
                        "source_page", "gorsel_url")
        ])
        points, _ = client.scroll(
            collection_name=COLLECTION, limit=200, with_payload=True,
            scroll_filter=_bank_filter(bank, url_match))
        if not points:
            return answer("Bu URL için parça bulunamadı (yanlış url ya da başka "
                          "bankaya ait olabilir).")

        rows = sorted(
            (int(((p.payload or {}).get("metadata", {}) or {}).get("chunk_index", 0) or 0),
             p.id,
             (p.payload or {}).get("metadata", {}) or {},
             ((p.payload or {}).get("page_content") or "").strip())
            for p in points)
        chars = sum(len(text) for _, _, _, text in rows)
        if chars > MAX_TOOL_CHARS:
            first = rows[0][1]
            return answer(_too_large(
                chars, len(rows),
                f"Bu belgede 0..{len(rows) - 1} arası parça var. İhtiyacın olan "
                f"yeri expand_chunk ile aç (ör. point_id={first}, after=3)."))

        body = _ENTRY_SEP.join(
            _entry(i + 1, pid, m, text, len(rows))
            for i, (_, pid, m, text) in enumerate(rows))
        return answer(body)

    return StructuredTool.from_function(
        func=_run, name="read_full_page", args_schema=_FullPageArgs,
        description=("Bir sayfanın/PDF'in TÜM parçalarını birleştirilmiş olarak "
                     "okur — elinde point_id değil sadece bir url varken. Bir "
                     "sonucun kesilmiş devamını okumak için önce expand_chunk'ı "
                     "dene: büyük belgelerin tamamı bağlama sığmaz ve o durumda "
                     "bu araç içerik yerine belgenin boyutunu bildirir."))


def build_bank_retrieval_tools(bank: str, marked: set, discarded: set) -> list[StructuredTool]:
    """Bir bankanın üç corpus aracı, hepsi aynı işaretleme kümelerini paylaşır."""
    return [
        make_bank_search_tool(bank, marked, discarded),
        make_expand_chunk_tool(bank, marked, discarded),
        make_full_page_tool(bank, marked, discarded),
    ]
