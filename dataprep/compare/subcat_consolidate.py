"""Alt-kategori konsolidasyon ajanı — TAMAMEN LLM kararı, string/embedding
benzerliği YOK. `data/_tables/` içindeki mevcut alt kategoriler (65) zaman
içinde parçalanmış (32'si tek tablolu) çünkü synth.py'nin yumuşak "uyuyorsa
aynısını kullan" kuralı LLM'in ihtiyatına bağlıydı ve bazen yanlış karar
verdi (örn. ithalat-finansmanı-2 kendi alt-kategorisini icat etti).

Bu script SADECE bir konsolidasyon PLANI üretir (dry-run) — data/_tables/ ya
da Qdrant'a hiçbir yazma yapmaz. LLM'e her alt kategorinin ALTINDAKİ GERÇEK
TABLO KONULARINI (topic + docstring) gösteriyoruz ki karar isim benzerliğine
değil, içerik uyumuna dayansın (örn. "yatırım hesapları" ile "yatırım fonu
teminatlı finansman" isim olarak yakın ama İÇERİK olarak farklı ürün
tipleri — LLM bunu ayırt etmeli).

Kullanım:
  python -m dataprep.compare.subcat_consolidate > /tmp/subcat_plan.json
"""
from __future__ import annotations

import glob
import json
import logging
from pathlib import Path

from dataprep import vlm

from . import store
from .retrieval import index_table

log = logging.getLogger("dataprep.compare.subcat_consolidate")

ROOT = Path(__file__).resolve().parents[2] / "data" / "_tables"
MAX_EXAMPLES = 10   # kalabalık subcategory'lerde (örn. 62 tablolu) temsili örnek sayısı

_Q = (
    "ÖNEMLİ — TERMİNOLOJİ: bunlar KATILIM BANKASI (faizsiz/İslami bankacılık) "
    "verileri.\n\n"
    "Aşağıda, bir karşılaştırma tablosu havuzunun MEVCUT alt kategorileri var — "
    "her biri zaman içinde ayrı ayrı, tutarsız isimlerle oluşturulmuş olabilir. "
    "Her alt kategorinin altında GERÇEK TABLO KONULARINDAN (topic + kısa "
    "docstring) örnekler de veriliyor.\n\n"
    "GÖREVİN: bu alt kategorileri incele ve HANGİLERİNİN AYNI ANLAMLI ürün/"
    "kampanya AİLESİNİ temsil ettiğini belirle — SADECE isim benzerliğine "
    "bakma, altındaki tablo konularının GERÇEKTEN aynı türde ürünler olup "
    "olmadığına bak. İki alt kategori isim olarak yakın görünse de (örn. "
    "'yatırım hesapları' vs 'yatırım fonu teminatlı finansman') içerik "
    "olarak FARKLI ürün tipleriyse (biri hesap, biri teminatlı finansman) "
    "AYRI tut — birleştirme yalnızca GERÇEKTEN aynı ürün ailesi olduğunda "
    "yapılır.\n\n"
    "Birleştirmeye karar verdiğin her grup için: kısa, kapsamlı ve net bir "
    "YENİ kanonik alt kategori adı üret (Türkçe, küçük harf, örnekteki "
    "stille tutarlı). Birleştirmediğin (tek başına doğru duran) alt "
    "kategoriler için kendi adını aynen koru.\n\n"
    "Alt kategoriler ve örnek tablo konuları:\n\"\"\"{payload}\"\"\"\n\n"
    "SADECE JSON döndür, şu şekilde:\n"
    '{{"groups": [{{"canonical_name": "<yeni ya da aynı kalan ad>", '
    '"merged_from": ["<eski alt kategori 1>", "<eski alt kategori 2>", ...], '
    '"reasoning": "<neden bu şekilde grupladığını 1-2 cümleyle açıkla>"}}, '
    "...]}}\n"
    "NOT: her mevcut alt kategori TAM OLARAK BİR grubun merged_from listesinde "
    "geçmeli (birleştirilmiyorsa da kendi adıyla tek elemanlı bir grup olarak "
    "listelenmeli) — hiçbiri atlanmasın.")


def _load_tables() -> list[dict]:
    files = [f for f in glob.glob(str(ROOT / "*.json"))
             if not Path(f).name.startswith("_")]
    return [json.loads(Path(f).read_text(encoding="utf-8")) for f in files]


def _build_payload(tables: list[dict]) -> dict[str, list[dict]]:
    by_sub: dict[str, list[dict]] = {}
    for t in tables:
        by_sub.setdefault(t.get("subcategory", ""), []).append(
            {"id": t["id"], "topic": t["topic"], "docstring": t["docstring"][:150]})
    for sub, items in by_sub.items():
        if len(items) > MAX_EXAMPLES:
            by_sub[sub] = items[:MAX_EXAMPLES] + [
                {"note": f"... ve {len(items) - MAX_EXAMPLES} tablo daha (toplam {len(items)})"}]
    return by_sub


def propose_plan() -> dict:
    tables = _load_tables()
    by_sub = _build_payload(tables)
    payload = json.dumps(
        {sub: [x.get("topic") or x.get("note") for x in items]
         for sub, items in by_sub.items()},
        ensure_ascii=False, indent=1)
    d = vlm.call_json(vlm.txt_msg(_Q.format(payload=payload)), max_tokens=4096)
    if not d:
        raise RuntimeError("LLM'e ulaşılamadı — plan üretilemedi.")
    groups = d.get("groups") or []

    # dogrulama: her eski subcategory TAM BIR grupta gecmeli
    all_subs = set(by_sub.keys())
    covered = set()
    for g in groups:
        covered.update(g.get("merged_from") or [])
    missing = all_subs - covered
    extra = covered - all_subs
    return {
        "total_old_subcategories": len(all_subs),
        "total_new_groups": len(groups),
        "merges": [g for g in groups if len(g.get("merged_from") or []) > 1],
        "unchanged": [g for g in groups if len(g.get("merged_from") or []) <= 1],
        "groups": groups,
        "warnings": {
            "subcategories_missing_from_plan": sorted(missing),
            "unknown_subcategories_in_plan": sorted(extra),
        },
        "tables_per_old_subcategory": {sub: len(items) for sub, items in
                                       ((s, [t for t in tables if t.get("subcategory") == s])
                                        for s in all_subs)},
    }


def apply_plan(plan: dict) -> dict:
    """Onaylanmış planı UYGULAR: data/_tables/*.json'daki subcategory alanlarını
    yeniden yazar (store.overwrite_table ile registry de senkron kalır), sonra
    değişen her tabloyu Qdrant'a yeniden embed eder (index_table). Kullanılmayan
    eski alt kategori adlarını _subcategories.json'dan temizler."""
    mapping: dict[str, str] = {}
    for g in plan.get("groups", []):
        canon = g["canonical_name"]
        for old in g.get("merged_from") or []:
            mapping[old] = canon

    changed = []
    files = [f for f in glob.glob(str(ROOT / "*.json")) if not Path(f).name.startswith("_")]
    for f in files:
        t = json.loads(Path(f).read_text(encoding="utf-8"))
        old_sub = t.get("subcategory", "")
        new_sub = mapping.get(old_sub, old_sub)
        if new_sub == old_sub:
            continue
        store.overwrite_table(
            t["id"], t["docstring"], t["columns"], t["rows"], t["sources"],
            t.get("category", ""), new_sub)
        index_table(t["id"], t["topic"], t.get("category", ""), new_sub, t["docstring"])
        changed.append({"id": t["id"], "from": old_sub, "to": new_sub})
        log.info("  %s: %r -> %r", t["id"], old_sub, new_sub)

    # kullanilmayan eski alt kategori adlarini temizle
    live_subs = set()
    for f in files:
        t = json.loads(Path(f).read_text(encoding="utf-8"))
        live_subs.add(t.get("subcategory", ""))
    all_subs = store.load_subcategories()
    pruned = [s for s in all_subs if s in live_subs]
    dropped = [s for s in all_subs if s not in live_subs]
    from .store import SUBCATS as _SUBCATS
    _SUBCATS.write_text(json.dumps(pruned, ensure_ascii=False, indent=1), encoding="utf-8")

    return {"tables_changed": len(changed), "changes": changed,
            "subcategories_dropped": dropped, "subcategories_remaining": len(pruned)}


def main() -> None:
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if "--apply" in sys.argv:
        plan_path = sys.argv[sys.argv.index("--apply") + 1]
        plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
        result = apply_plan(plan)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        log.info("UYGULANDI: %d tablo güncellendi, %d eski alt kategori düşürüldü, "
                  "%d alt kategori kaldı.", result["tables_changed"],
                  len(result["subcategories_dropped"]), result["subcategories_remaining"])
        return

    plan = propose_plan()
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    log.info("PLAN (dry-run): %d eski alt kategori -> %d grup (%d birleştirme). "
              "Hiçbir dosya/Qdrant DEĞİŞTİRİLMEDİ.",
              plan["total_old_subcategories"], plan["total_new_groups"], len(plan["merges"]))
    if plan["warnings"]["subcategories_missing_from_plan"]:
        log.warning("UYARI: plana dahil edilmeyen alt kategoriler: %s",
                     plan["warnings"]["subcategories_missing_from_plan"])


if __name__ == "__main__":
    main()
