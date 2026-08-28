"""SAYFA KAPSAMLI TARİH ARAŞTIRMA AJANI.

tablo_tarih.py'nin 3. adımı: kod ile (cell_sources kaydı → frontmatter →
deterministik çıkarım) tarihi bulunamayan hücreler için belgeyi ARAŞTIRAN bir
ajana sorar.

KAPSAM — SADECE O HÜCRENİN KAYNAK SAYFASI: banka geneli arama YOK. Ajan yalnız o URL'in içinde kalır; oradan çıkamaz.
Sayfanın içinde ise serbesttir:

  sayfa_metni()    — sayfanın TÜM metin chunk'ları, chunk_index sırasıyla,
                     birleştirilmiş tam metin (kırpma yok).
  sayfa_gorselleri() — o sayfada geçen görsellerin okunmuş içeriği. Kampanya
                     tarihleri çok sık görselin üstünde yazar ("31 Aralık'a
                     kadar"), metinde hiç geçmez — bu yüzden ayrı araç.
  chunk_oku()      — tek bir chunk'ı ham haliyle, komşularıyla birlikte okur
                     (metin çok uzunsa ajan parça parça ilerlemek isteyebilir).

Ajan bu ipuçlarından tarihi ÇIKARABİLİR: "kampanya 1 Ağustos'ta başladı",
"son başvuru 30 Eylül", görselde "31.12.2026'ya kadar" gibi. Yıl yazmıyorsa
belgenin kendi bağlamından (yayın tarihi) yıl tamamlanabilir — ama UYDURMA
yasak: emin değilse boş bırakır.

Bağlantı dayanıklılığı bank_agent/tablo_denetim ile AYNI: sınırsız retry,
her hatada önce tünel kontrolü sonra taze havuz, NET_SEM paylaşımı.
Uzunluk/karakter sınırı YOK.
"""
from __future__ import annotations

import json
import logging
import os
import time

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from qdrant_client import models

from config import tunnel
from llm import get_llm
from llm.providers.vllm_provider import reset_http_pool

from ..net_limit import NET_SEM
from .json_mod import llm_kwargs
from .retrieval import COLLECTION, _shared, _url_kosulu

log = logging.getLogger("dataprep.compare.tarih_ajan")

MAX_TOOL_CALLS = int(os.environ.get("TARIH_MAX_TOOL_CALLS", "100"))
_BACKOFF_MAX = float(os.environ.get("VLM_BACKOFF_MAX", "30"))
_TEMP_LADDER = (0.0, 0.3, 0.6, 1.0)

_SYSTEM = (
    "Sen bir katılım bankası belgesinin GEÇERLİLİK TARİHLERİNİ bulan "
    "araştırmacısın. Sana TEK BİR SAYFA verildi ve yalnız o sayfanın içinde "
    "çalışırsın; başka sayfaya geçemezsin.\n\n"
    "ARAÇLARIN:\n"
    "- sayfa_metni: sayfanın tam metni.\n"
    "- sayfa_gorselleri: sayfadaki görsellerin okunmuş içeriği. Kampanya "
    "tarihleri ÇOK SIK sadece görselde yazar; metinde tarih yoksa MUTLAKA "
    "buraya bak.\n"
    "- chunk_oku: belirli bir parçayı komşularıyla okur.\n\n"
    "NASIL ÇALIŞIRSIN — PARÇA PARÇA (chunk by chunk): sayfa numaralı "
    "parçalara bölünmüştür. Sayfanın TAMAMINI, parçaları tek tek geçerek "
    "incelemen gerekir; tarih ilk parçada da olabilir, en sondaki dipnotta da. "
    "Hiçbir parçayı atlama. Görseller de aynı şekilde tek tek incelenir.\n\n"
    "NE ARIYORSUN: bu üründe/kampanyada geçerlilik başlangıcı ve bitişi. "
    "İpuçları: 'X tarihine kadar', 'son başvuru', 'kampanya süresi', "
    "'X - Y tarihleri arasında', 'geçerlilik', 'yürürlük', dipnotlar, "
    "görsel üzerindeki tarih damgaları.\n\n"
    "TARİH BULAMAMAK BAŞARISIZLIK DEĞİLDİR. Belgelerin çoğunda tarih YOKTUR "
    "(daimi ürünler, ücret tarifeleri, bilgi sayfaları). 'Bulamadım' demek "
    "TAMAMEN GEÇERLİ ve SIK VERİLEN bir cevaptır. Alanları doldurmak zorunda "
    "değilsin — emin olmadığın hiçbir şeyi yazma.\n\n"
    "ASLA TAHMİN ETME: bugünün tarihi, kendi bilgin ya da 'muhtemelen şu "
    "yıldır' akıl yürütmesi YASAK. Yalnız belgede AÇIKÇA yazan geçerlidir. "
    "Örneğin görselde yılsız '1-31 Aralık' yazıyorsa ve yıl belgede hiçbir "
    "yerde belirtilmemişse, o tarihi YAZMA — 'bulunamadi' de. Yanlış tarih, "
    "tarih yokluğundan çok daha kötüdür.\n\n"
    "Araştırman bitince SADECE şu JSON'u döndür:\n"
    '  {"durum": "<bulundu | bulunamadi | suresiz>", '
    '"gecerlilik_baslangic": "<YYYY-MM-DD ya da boş>", '
    '"gecerlilik_bitis": "<YYYY-MM-DD ya da boş>", '
    '"kanit": "<neye dayandın; bulamadıysan nereye baktığını yaz>"}\n\n'
    "durum alanı:\n"
    "- 'bulundu': belgede açıkça yazan tarih(ler)i buldun. DÖRT DURUM DA "
    "geçerlidir: yalnız başlangıç, yalnız bitiş, ikisi birden, ya da hiçbiri. "
    "Yalnız biri yazıyorsa diğerini BOŞ bırak — eksik olan tarafı tamamlamaya "
    "çalışma.\n"
    "- 'suresiz': ürün daimi, geçerlilik tarihi kavramı yok.\n"
    "- 'bulunamadi': aradın ama açıkça yazan tarih yok. Tarih alanlarını BOŞ bırak."
)


def _kalici_hata(exc: Exception) -> bool:
    s = str(exc)
    return any(k in s for k in ("401", "404", "413", "422", "BadRequest"))


def _invoke(tools, messages, allow_tools: bool = True):
    """SINIRSIZ retry. Her hatada önce tünel kontrolü, sonra taze havuz."""
    start = time.time()
    delay = 1.0
    attempt = 0
    last_warn = 0.0
    while True:
        t = _TEMP_LADDER[min(attempt, len(_TEMP_LADDER) - 1)]
        try:
            # Araç bağlanmayacaksa düz JSON bekleniyor demektir -> vLLM'e
            # JSON zorlaması geç (bkz. json_mod.py). Araçla BİRLİKTE
            # kullanılmaz: JSON'a zorlanan model tool_calls üretemez.
            _arac_var = bool(allow_tools and tools)
            llm = get_llm("gemma", temperature=t, **llm_kwargs(not _arac_var))
            if _arac_var:
                llm = llm.bind_tools(tools)
            _t0 = time.time()
            with NET_SEM:
                res = llm.invoke(messages)
            NET_SEM.report(ok=True, duration=time.time() - _t0)
            return res
        except Exception as exc:
            if _kalici_hata(exc):
                log.error("    [KALICI HATA] tarih ajanı: %s", exc)
                return None
            NET_SEM.report(ok=False)
            reset_http_pool(f"tarih_ajan/{type(exc).__name__}")
            tunnel.refresh_if_needed()
            elapsed = time.time() - start
            if elapsed - last_warn >= 300:
                log.warning("    [TARIH_AJAN_UZUN_HATA] %.0fs'dir başarısız "
                            "(deneme %d) — DEVAM ediyor", elapsed, attempt + 1)
                last_warn = elapsed
            time.sleep(delay)
            delay = min(delay * 2, _BACKOFF_MAX)
            attempt += 1


def _sayfa_noktalari(url: str, tip: str) -> list:
    """Bu URL'e ait, verilen tipteki TÜM noktalar, chunk_index sırasıyla."""
    _, client = _shared()
    kosul = _url_kosulu(url)
    kosul.must = [models.FieldCondition(key="metadata.type",
                                         match=models.MatchValue(value=tip))]
    out, off = [], None
    while True:
        pts, off = client.scroll(collection_name=COLLECTION, scroll_filter=kosul,
                                  limit=256, offset=off, with_payload=True)
        out.extend(pts)
        if not off:
            break
    def _ix(p):
        m = (p.payload or {}).get("metadata", {}) or {}
        return (m.get("chunk_index", 0), m.get("gorsel_index", 0))
    return sorted(out, key=_ix)


def _icerik(p) -> str:
    pl = p.payload or {}
    return pl.get("page_content") or pl.get("text") or ""


def _araclar(url: str) -> list[StructuredTool]:
    """Üç araç da bu URL'e KİLİTLİ (closure) — ajan sayfadan çıkamaz."""

    def _metin(_dummy: str = "") -> str:
        """Sayfanın TÜM parçaları, SINIRLARI GÖRÜNÜR biçimde.

        Parça sınırları etiketli veriliyor ki model sayfayı parça parça
        tarayabilsin ve hiçbirini atlamadığından emin olsun. Kırpma YOK —
        parçaların tamamı burada."""
        pts = _sayfa_noktalari(url, "metin")
        if not pts:
            return "Bu sayfanın metni indekste bulunamadı."
        n = len(pts)
        bas = [f"Bu sayfa {n} parçadan oluşuyor. HEPSİNİ tek tek incele; "
               f"tarih herhangi bir parçada olabilir."]
        for i, pt in enumerate(pts):
            bas.append(f"--- parça {i}/{n - 1} ---\n{_icerik(pt)}")
        return "\n\n".join(bas)

    def _gorseller(_dummy: str = "") -> str:
        pts = _sayfa_noktalari(url, "gorsel")
        if not pts:
            return "Bu sayfada indekslenmiş görsel yok."
        parcalar = [f"Bu sayfada {len(pts)} görsel var; HEPSİNE bak."]
        for i, p in enumerate(pts):
            m = (p.payload or {}).get("metadata", {}) or {}
            parcalar.append(f"--- görsel {i}/{len(pts) - 1}: "
                            f"{m.get('gorsel_kaynak', '?')} ---\n{_icerik(p)}")
        return "\n\n".join(parcalar)

    def _chunk(chunk_index: int = 0) -> str:
        pts = _sayfa_noktalari(url, "metin")
        if not pts:
            return "Bu sayfanın metni indekste bulunamadı."
        n = len(pts)
        i = max(0, min(int(chunk_index), n - 1))
        alt, ust = max(0, i - 1), min(n, i + 2)
        return "\n\n".join(
            f"[parça {j}/{n - 1}]\n{_icerik(pts[j])}" for j in range(alt, ust))

    return [
        StructuredTool.from_function(
            func=_metin, name="sayfa_metni",
            description="Bu sayfanın TAM metnini döndürür (tüm parçalar birleşik)."),
        StructuredTool.from_function(
            func=_gorseller, name="sayfa_gorselleri",
            description="Bu sayfadaki görsellerin okunmuş içeriğini döndürür. "
                        "Kampanya tarihleri sık sık SADECE görselde yazar."),
        StructuredTool.from_function(
            func=_chunk, name="chunk_oku",
            description="Sayfanın belirli bir parçasını komşularıyla okur "
                        "(chunk_index: 0'dan başlar)."),
    ]


def _json_ayikla(metin: str) -> dict | None:
    if not metin:
        return None
    s = metin.strip()
    if s.startswith("```"):
        s = s.split("```")[1]
        s = s[4:] if s.lower().startswith("json") else s
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        d = json.loads(s[i:j + 1])
    except ValueError:
        return None
    return d if isinstance(d, dict) else None



def sayfa_tarihi(url: str, bank: str = "", baglam: str = "") -> tuple[str, str]:
    """Bu sayfanın geçerlilik tarihini araştırarak bulur.

    Döner: (baslangic, bitis) — ISO (YYYY-MM-DD) ya da boş."""
    tools = _araclar(url)
    ipucu = f"\n\nBu belge şu tablo hücresinin kaynağı: {baglam}" if baglam else ""
    messages = [
        SystemMessage(_SYSTEM),
        HumanMessage(f"Araştırılacak sayfa: {url}{ipucu}\n\n"
                     "Önce sayfa_metni ile metne bak. Tarih bulamazsan "
                     "sayfa_gorselleri ile görselleri incele. Sonra JSON'u ver."),
    ]
    by_name = {t.name: t for t in tools}
    for _ in range(MAX_TOOL_CALLS):
        ai = _invoke(tools, messages)
        if ai is None:
            return "", ""
        messages.append(ai)
        if not getattr(ai, "tool_calls", None):
            d = _json_ayikla(getattr(ai, "content", "") or "")
            if d is None:
                messages.append(HumanMessage(
                    "Cevabın geçerli JSON değildi. SADECE istenen JSON'u döndür."))
                continue
            b = (d.get("gecerlilik_baslangic") or "").strip()
            s = (d.get("gecerlilik_bitis") or "").strip()
            durum = (d.get("durum") or "").strip() or ("bulundu" if (b or s) else "bulunamadi")
            log.info("    [TARİH:%s] %s -> %s / %s (%s)", durum, url,
                     b or "?", s or "?", (d.get("kanit") or "")[:80])
            return b, s
        for tc in ai.tool_calls:
            fn = by_name.get(tc.get("name"))
            try:
                sonuc = fn.invoke(tc.get("args") or {}) if fn else \
                    f"Bilinmeyen araç: {tc.get('name')}"
            except Exception as exc:
                sonuc = f"HATA: {exc}"
            messages.append(ToolMessage(content=str(sonuc), tool_call_id=tc.get("id")))
    log.warning("    tarih ajanı %s için araç limitine takıldı", url)
    return "", ""
