"""Tablo havuzu bakım scripti: bir hücrenin TÜM kaynakları süresi geçmişse o
hücrenin DEĞERİNİ temizler — TAMAMEN LLM'siz, deterministik tarih matematiği
(dedup.py'nin "elle çalıştırılır bakım ajanı" desenini izler, ama LLM çağrısı
YOK). Tablo üretim pipeline'ından (pipeline.py) TAMAMEN BAĞIMSIZ — istendiği
zaman (periyodik/manuel rerun) çalıştırılabilir, ana akışı ETKİLEMEZ.

Bir hücre `cell_sources[bank][col]` altında bir veya daha fazla kaynak (point_id
+ url + gecerlilik_baslangic/bitis + validity_status) taşır. HEPSİ süresi
geçmişse hücre değeri görünür bir işaretle temizlenir (sessizce boşaltılmaz —
NEDEN boş olduğu iz bırakır); `cell_sources` kaydı KORUNUR (iz kaybolmaz,
sonradan yeniden doğrulanabilir). Herhangi bir kaynak hâlâ geçerli/bilinmiyorsa
hücreye DOKUNULMAZ (belirsizlikte veri kaybı yok ilkesi).

Orijinal içerik her zaman `cell_sources`'taki `url` üzerinden
data/<bank>_site/content/*.md'den erişilebilir kalır — tabloya ayrıca
kopyalanmaz.

Kullanım:
  python -m dataprep.compare.expire_check
"""
from __future__ import annotations

import logging

from corpus import dates

from . import store

log = logging.getLogger("dataprep.compare.expire_check")

EXPIRED_MARK = "(kaynaklar süresi geçti, doğrulanmalı)"


def _source_expired(src: dict) -> bool:
    end = src.get("gecerlilik_bitis")
    if end:
        return not dates.is_active(end)
    return src.get("validity_status") == "suresi_gecmis"


def _cell_all_expired(sources: list[dict]) -> bool:
    return bool(sources) and all(_source_expired(s) for s in sources)


def check_table(table: dict) -> int:
    """Bir tablonun cell_sources'ını tarar, TÜMÜ süresi geçmiş hücreleri
    işaretler. Değişen hücre sayısını döner (0 ise dokunulmadı)."""
    cell_sources = table.get("cell_sources") or {}
    rows = table.get("rows") or {}
    changed = 0
    for bank, cols in cell_sources.items():
        bank_row = rows.get(bank)
        if bank_row is None:
            continue
        for col, sources in cols.items():
            if col not in bank_row:
                continue
            current = bank_row[col]
            if not current or current == EXPIRED_MARK:
                continue
            if _cell_all_expired(sources):
                bank_row[col] = EXPIRED_MARK
                changed += 1
    return changed


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    registry = store.load_registry()
    total_tables = 0
    total_cells = 0
    for r in registry:
        table = store.load_table(r["id"])
        if table is None:
            continue
        changed = check_table(table)
        if changed:
            store.overwrite_table(table["id"], table["docstring"], table["columns"],
                                    table["rows"], table.get("sources", {}),
                                    table["category"], table["subcategory"],
                                    cell_sources=table.get("cell_sources"))
            total_tables += 1
            total_cells += changed
            log.info("[SÜRESİ GEÇTİ] %s: %d hücre işaretlendi", table["id"], changed)
    log.info("BİTTİ: %d tablo, %d hücre işaretlendi", total_tables, total_cells)


if __name__ == "__main__":
    main()
