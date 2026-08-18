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
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "data" / "_tables"
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


def subcategory_examples(limit: int = 5) -> dict[str, list[str]]:
    """Her alt kategori için örnek tablo docstring'leri — synth.py'nin yeni tablo/
    birleştirme kararında SADECE alt kategori adına değil, altındaki GERÇEK
    tabloların neyi kapsadığına bakabilmesi için (isim benzerliği yeterli değil,
    içerik uyumu gerekiyor)."""
    by_sub: dict[str, list[str]] = {}
    for r in load_registry():
        by_sub.setdefault(r.get("subcategory", ""), []).append(r.get("docstring", ""))
    return {k: v[:limit] for k, v in by_sub.items() if k}


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


def create_table(topic: str, docstring: str, columns: list[str], rows: dict,
                  sources: dict, category: str = "", subcategory: str = "") -> str:
    """Yeni tablo dosyası + registry kaydı. Dönen: table_id."""
    with _lock:
        table_id = _slugify(topic)
        base = table_id
        i = 2
        while (ROOT / f"{table_id}.json").exists():
            table_id = f"{base}-{i}"; i += 1
        table = {"id": table_id, "topic": topic, "docstring": docstring,
                  "category": category, "subcategory": subcategory,
                  "columns": columns, "rows": rows, "sources": sources,
                  "created_at": _now(), "updated_at": _now()}
        ROOT.mkdir(parents=True, exist_ok=True)
        (ROOT / f"{table_id}.json").write_text(
            json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")
        reg = load_registry()
        reg.append({"id": table_id, "docstring": docstring,
                     "category": category, "subcategory": subcategory})
        _save_registry(reg)
    register_subcategory(subcategory)
    return table_id


def add_row(table_id: str, bank: str, values: dict, sources: list[dict]) -> None:
    """Mevcut tabloya (ya da eksik sütunlarına) bir bankanın satırını ekler/günceller."""
    with _lock:
        table = load_table(table_id)
        if table is None:
            return
        table["rows"][bank] = values
        table["sources"][bank] = sources
        for col in values:
            if col not in table["columns"]:
                table["columns"].append(col)
        table["updated_at"] = _now()
        (ROOT / f"{table_id}.json").write_text(
            json.dumps(table, ensure_ascii=False, indent=1), encoding="utf-8")


def overwrite_table(table_id: str, docstring: str, columns: list[str], rows: dict,
                      sources: dict, category: str = "", subcategory: str = "") -> None:
    """Mevcut bir tablonun İÇERİĞİNİ TAMAMEN değiştirir (id/created_at korunur) —
    dedup.py'nin birleştirme adımı tarafından kullanılır (iki tablo tek tabloda
    toplanınca, kalan tarafın içeriği bu yeni birleşik içerikle değişir)."""
    with _lock:
        table = load_table(table_id)
        if table is None:
            return
        table["docstring"] = docstring
        table["columns"] = columns
        table["rows"] = rows
        table["sources"] = sources
        table["category"] = category
        table["subcategory"] = subcategory
        table["updated_at"] = _now()
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
