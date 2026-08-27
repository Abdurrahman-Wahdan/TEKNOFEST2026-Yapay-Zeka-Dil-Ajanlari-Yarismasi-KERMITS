"""Tablo deposu: data/_tables/{id}.json (docstring+kategori+sütun+satır+kaynak) +
_registry.json (id->docstring, match_table için) + _subcategories.json (UI alt-
kategori tutarlılığı için) + _page_ledger.json (sayfa URL -> kayıt).

Ledger'da İKİ AYRI şey var, karıştırılmaz:
  * own_verdict: ANA TRAVERSAL bu sayfayı KENDİSİ anchor olarak inceleyip karar
    verdiyse true. SADECE bu, traversal'ın "zaten işlendi, atla" kontrolünde
    kullanılır (page_verdict()).
  * cited_tables: bu sayfa bir subagent'ın RETRIEVE'inde kanıt olarak geçtiyse
    (başka bir konunun araştırmasında). Bu BİLGİ amaçlıdır — traversal'ın kendi
    sırasında bu sayfaya geldiğinde ATLAMASINI SAĞLAMAZ; sadece görüldü kaydı.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "data" / "_tables"

log = logging.getLogger(__name__)
REGISTRY = ROOT / "_registry.json"
LEDGER = ROOT / "_page_ledger.json"
SUBCATS = ROOT / "_subcategories.json"
_lock = threading.Lock()


def _slugify(topic: str) -> str:
    s = re.sub(r"[^\w\s-]", "", topic.lower()).strip()
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:60] or "konu"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- registry (id -> docstring, match_table için hızlı liste) ---------------
def load_registry() -> list[dict]:
    if not REGISTRY.exists():
        return []
    return json.loads(REGISTRY.read_text(encoding="utf-8"))


def _save_registry(reg: list[dict]) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(reg, ensure_ascii=False, indent=1), encoding="utf-8")


# --- alt kategori registry'si (UI tutarlılığı) -------------------------------
def load_subcategories() -> list[str]:
    return json.loads(SUBCATS.read_text(encoding="utf-8")) if SUBCATS.exists() else []


def register_subcategory(cat: str) -> None:
    if not cat:
        return
    with _lock:
        cats = load_subcategories()
        if cat not in cats:
            cats.append(cat)
            ROOT.mkdir(parents=True, exist_ok=True)
            SUBCATS.write_text(json.dumps(cats, ensure_ascii=False, indent=1), encoding="utf-8")


# --- tablo dosyaları ---------------------------------------------------------
def load_table(table_id: str) -> dict | None:
    p = ROOT / f"{table_id}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _damgala(table: dict) -> None:
    """Tabloyu diske yazmadan hemen once tarih damgalar.

    VARSAYILAN OLARAK KAPALI (TABLO_ANLIK_TARIH=1 ile acilir). Karar
    (kullanici, 2026-08-19): URL ve tarih baglamasi EN SONDA, TOPLUCA ve
    agentic yapilacak — cunku bir hucreye o bankanin rastgele bir kaynagini
    baglamak ALAKASIZ bir URL/tarih damgalayabiliyor. Bu asamada yalnizca
    KAYNAK HAVUZU biriktirilir (cell_sources + sources). Her veri sutununun yanina '<sutun> (Gecerlilik)' sutunu
    girer; kaynagi olmayan hucre de '-' alir, yani ALAN HER ZAMAN VAR.

    Import DONGUYU kirmak icin fonksiyon icinde yapilir (tablo_tarih ->
    store bagimliligi var). Damgalama basarisiz olursa tablo yine de
    KAYDEDILIR — tarih eksikligi veri kaybindan iyidir, sonradan
    `python3 -m dataprep.compare.tablo_tarih` ile tamamlanabilir."""
    if os.environ.get("TABLO_ANLIK_TARIH") != "1":
        return                                     # varsayilan: KAPALI (bkz. docstring)
    try:
        from . import tablo_tarih
        tablo_tarih.tabloyu_damgala(table)
    except Exception as exc:                       # pragma: no cover
        log.warning("  [TARIH DAMGA HATASI] %s: %s: %s",
                    table.get("id", "?"), type(exc).__name__, exc)


def create_table(topic: str, docstring: str, columns: list[str], rows: dict,
                  sources: dict, category: str = "", subcategory: str = "",
                  created_from: dict | None = None, cell_sources: dict | None = None) -> str:
    """Yeni tablo dosyası + registry kaydı. Dönen: table_id.

    created_from: bu tabloyu İLK tetikleyen (banka, url) — {"bank": ..., "url": ...}.
    Sadece burada, tablo İLK kurulurken yazılır; sonraki eksik-banka ekleme ya
    da merge'lerde DEĞİŞMEZ (overwrite_table bu alanı kabul etmez), böylece
    hep tablonun kökenini gösterir.

    cell_sources: {banka: {sütun: [source_dict, ...]}} — `sources` (banka
    bazlı) ile PARALEL, HÜCRE bazlı iz sürme; sütun bazında hangi point_id/url/
    tarihten geldiğini taşır. expire_check.py bunu kullanır."""
    with _lock:
        table_id = _slugify(topic)
        base = table_id
        i = 2
        while (ROOT / f"{table_id}.json").exists():
            table_id = f"{base}-{i}"; i += 1
        table = {"id": table_id, "topic": topic, "docstring": docstring,
                  "category": category, "subcategory": subcategory,
                  "columns": columns, "rows": rows, "sources": sources,
                  "cell_sources": cell_sources or {},
                  "created_from": created_from or {},
                  "created_at": _now(), "updated_at": _now()}
        _damgala(table)
        ROOT.mkdir(parents=True, exist_ok=True)
        (ROOT / f"{table_id}.json").write_text(
            json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")
        reg = load_registry()
        reg.append({"id": table_id, "docstring": docstring,
                     "category": category, "subcategory": subcategory})
        _save_registry(reg)
    register_subcategory(subcategory)
    return table_id


def overwrite_table(table_id: str, docstring: str, columns: list[str], rows: dict,
                      sources: dict, category: str = "", subcategory: str = "",
                      cell_sources: dict | None = None) -> None:
    """Mevcut bir tablonun İÇERİĞİNİ TAMAMEN değiştirir (id/created_at korunur) —
    dedup.py'nin birleştirme adımı tarafından kullanılır (iki tablo tek tabloda
    toplanınca, kalan tarafın içeriği bu yeni birleşik içerikle değişir).
    cell_sources: verilmezse (None) mevcut değer KORUNUR — çağıranların hepsi
    bu alanı bilmek zorunda değil."""
    with _lock:
        table = load_table(table_id)
        if table is None:
            return
        table["docstring"] = docstring
        table["columns"] = columns
        table["rows"] = rows
        table["sources"] = sources
        if cell_sources is not None:
            table["cell_sources"] = cell_sources
        table["category"] = category
        table["subcategory"] = subcategory
        table["updated_at"] = _now()
        _damgala(table)
        (ROOT / f"{table_id}.json").write_text(
            json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")
        reg = load_registry()
        for r in reg:
            if r["id"] == table_id:
                r["docstring"] = docstring
                r["category"] = category
                r["subcategory"] = subcategory
                break
        _save_registry(reg)
    register_subcategory(subcategory)


def delete_table(table_id: str) -> None:
    """Bir tablo dosyasını ve registry kaydını SİLER — dedup.py'nin birleştirme
    sonrası artık gereksiz kalan (kaybeden) tabloyu kaldırması için."""
    with _lock:
        p = ROOT / f"{table_id}.json"
        if p.exists():
            p.unlink()
        reg = load_registry()
        reg = [r for r in reg if r["id"] != table_id]
        _save_registry(reg)


def remap_ledger_table(old_id: str, new_id: str) -> None:
    """Ledger'daki (sayfa -> tablo) referanslarını, silinen bir tablonun id'sinden
    kalan tabloya taşır — dedup.py birleştirdikten sonra kaynak izleri kopmasın."""
    with _lock:
        led = _load_ledger()
        for rec in led.values():
            for key in ("tables", "cited_tables"):
                lst = rec.get(key)
                if isinstance(lst, list) and old_id in lst:
                    rec[key] = [new_id if x == old_id else x for x in lst]
        _save_ledger(led)


# --- banka bazlı BENZERSİZ URL havuzu -----------------------------------------
# NEDEN: URL/tarih baglamasi EN SONDA, toplu ve agentic yapilacak (kullanici
# karari 2026-08-19) — cunku bir hucreye o bankanin rastgele bir kaynagini
# baglamak ALAKASIZ URL/tarih damgalayabiliyor. Bu asamada tablolar uretilirken
# gorulen HER kaynak, BANKA BAZINDA ve BENZERSIZ olarak burada biriktirilir;
# sondaki eslestirme adiminin girdisi budur.
#
# Ayni URL bircok hucrede/tabloda tekrar gecer (bir bilgilendirme formu o
# bankanin 8 sutununun da kaynagi olabilir) — havuz bunu TEK kayda indirger,
# hangi tablolarda/point_id'lerde gorundugunu koruyarak.
URL_HAVUZ = ROOT / "_url_havuzu.json"


def load_url_pool() -> dict:
    """{banka: {url: {point_ids, tables, gecerlilik_baslangic, ...}}}"""
    if not URL_HAVUZ.exists():
        return {}
    try:
        return json.loads(URL_HAVUZ.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_url_pool(d: dict) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = URL_HAVUZ.with_suffix(".json.tmp")       # atomik yazma: yarim dosya kalmasin
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, URL_HAVUZ)


def record_url_pool(sources: dict, table_id: str = "") -> None:
    """`{banka: [source_dict, ...]}` kayitlarini havuza BENZERSIZ ekler.

    Ayni (banka, url) tekrar gelirse yeni point_id/tablo bilgisi mevcut kayda
    EKLENIR, kayit COGALMAZ. Tarih alanlari yalnizca BOSSA doldurulur — daha
    once dolu bir tarih asla ezilmez."""
    if not sources:
        return
    with _lock:
        havuz = load_url_pool()
        degisti = False
        for banka, kayitlar in (sources or {}).items():
            if not banka or not isinstance(kayitlar, list):
                continue
            banka_havuz = havuz.setdefault(banka, {})
            for src in kayitlar:
                if not isinstance(src, dict):
                    continue
                url = (src.get("url") or "").strip()
                if not url:
                    continue                       # URL'siz kaynak havuza girmez
                kayit = banka_havuz.setdefault(url, {
                    "point_ids": [], "tables": [],
                    "gecerlilik_baslangic": "", "gecerlilik_bitis": "",
                    "validity_status": "", "ilk_gorulme": _now(),
                })
                pid = (src.get("point_id") or "").strip()
                if pid and pid not in kayit["point_ids"]:
                    kayit["point_ids"].append(pid); degisti = True
                if table_id and table_id not in kayit["tables"]:
                    kayit["tables"].append(table_id); degisti = True
                for alan in ("gecerlilik_baslangic", "gecerlilik_bitis",
                              "validity_status"):
                    if not kayit.get(alan) and src.get(alan):
                        kayit[alan] = src[alan]; degisti = True
        if degisti:
            _save_url_pool(havuz)


# --- indekslenmemiş tablo kuyruğu ---------------------------------------------
# NEDEN: index_table geçici bir ağ/tünel hatasıyla patlarsa tablo diske yazılır
# ama arama havuzuna GİRMEZ. İndekssiz tablo mükerrerlik kontrolünde GÖRÜNMEZ
# olur ve aynı konuda ikinci bir tablo açılır (kanıtlı: 'kasko sigortası' 4 kez).
# Eskiden bu durum yalnızca loglanıp UNUTULUYORDU; dışarıdan bir nöbetçi script
# gerekiyordu. Artık başarısızlık burada KAYDEDİLİR ve pipeline bir sonraki
# tabloyu yazarken kuyruğu kendisi boşaltır — dış müdahale gerekmez.
INDEKS_KUYRUK = ROOT / "_indeks_kuyrugu.json"


def load_index_queue() -> list[str]:
    if not INDEKS_KUYRUK.exists():
        return []
    try:
        return json.loads(INDEKS_KUYRUK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save_index_queue(ids: list[str]) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = INDEKS_KUYRUK.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ids, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, INDEKS_KUYRUK)


def queue_for_index(table_id: str) -> None:
    """İndekslenemeyen tabloyu kuyruğa al (aynı id iki kez girmez)."""
    if not table_id:
        return
    with _lock:
        q = load_index_queue()
        if table_id not in q:
            q.append(table_id)
            _save_index_queue(q)


def dequeue_index(table_id: str) -> None:
    """Başarıyla indekslenen tabloyu kuyruktan düşür."""
    with _lock:
        q = load_index_queue()
        if table_id in q:
            q.remove(table_id)
            _save_index_queue(q)


# --- sayfa ledger'ı -----------------------------------------------------------
def _load_ledger() -> dict:
    return json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {}


def _save_ledger(d: dict) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def page_verdict(url: str) -> dict | None:
    """Ana traversal'ın BU sayfayı kendisi anchor olarak inceleyip verdiği karar
    (own_verdict=true) — SADECE bu, "zaten işlendi" skip kontrolünde kullanılır.
    Sayfa yalnızca bir başka konunun retrieve'inde kaynak olarak geçtiyse (own_verdict
    yok) None döner — traversal bu sayfaya kendi sırasında geldiğinde YİNE işler."""
    entry = _load_ledger().get(url)
    return entry if entry and entry.get("own_verdict") else None


def record_verdict(url: str, comparable: bool, topic: str, table_id: str | None) -> None:
    """Ana traversal'ın BU sayfa için kendi kararı. own_verdict=true işaretlenir —
    traversal bu URL'e tekrar gelirse artık atlar."""
    with _lock:
        led = _load_ledger()
        entry = led.get(url, {"tables": [], "cited_tables": []})
        entry["own_verdict"] = True
        entry["comparable"] = comparable
        entry["topic"] = topic
        if table_id and table_id not in entry.setdefault("tables", []):
            entry["tables"].append(table_id)
        led[url] = entry
        _save_ledger(led)


def record_citation(url: str, table_id: str) -> None:
    """Bu sayfa bir subagent'ın retrieve'inde KANIT olarak geçti — bilgi amaçlı,
    own_verdict SET ETMEZ, traversal'ın kendi sırasında bu sayfayı atlamasına
    yol AÇMAZ."""
    with _lock:
        led = _load_ledger()
        entry = led.get(url, {"tables": [], "cited_tables": []})
        entry.setdefault("cited_tables", [])
        if table_id not in entry["cited_tables"]:
            entry["cited_tables"].append(table_id)
        led[url] = entry
        _save_ledger(led)
