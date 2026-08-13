"""Keşfedilen endpoint'leri GEMMA ile ELE — müşteriye fayda sağlayanları TUT, gerisini SİL.

discover_endpoints.py ham+tekil endpoint'leri çıkarır (regex ile kaba 'relevant' işareti).
Bu adım her tekil endpoint'i (method, URL, istek örneği, yanıt örneği) GEMMA'ya verir:
"Bu, müşteriye DOĞRUDAN fayda sağlayan bir ARAÇ endpoint'i mi (kâr payı/finansman/
kart taksit hesaplama, döviz/altın kuru-çevirici, oran/fiyat sorgulama...) yoksa
altyapı/analytics/tracking/çerez/oturum/chatbot/config ÇÖPÜ mü?"

TUT -> endpoints_kept.json + endpoints_kept.md
SİL -> endpoints.json'dan çıkarılır (yalnız KEEP'ler kalır); raw_calls.jsonl'e dokunulmaz
       (ham kanıt saklanır). --prune ile endpoints.json de KEEP'lere indirgenir.

Kullanım:
  python -m dataprep.discovery.filter_endpoints            # ele, kept dosyalarını yaz
  python -m dataprep.discovery.filter_endpoints --prune    # endpoints.json'ı da KEEP'e indir
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from dataprep import vlm

OUT = Path(__file__).resolve().parent / "out"

_Q = (
    "Bir KATILIM BANKASI web sitesinde yakalanmış CANLI bir HTTP endpoint'ini "
    "değerlendiriyorsun. Bir sohbet asistanı, müşteriye bilgi sunmak için bu endpoint'i "
    "ÇAĞIRABİLİR. Asistanın elinde ZATEN statik ürün/kampanya METNİ var (arama motorunda "
    "gömülü); dolayısıyla amacımız onun VEREMEDİĞİ şeyi yakalamak: DİNAMİK/GERÇEK-ZAMANLI "
    "veya GİRDİYE-BAĞLI yapılandırılmış veri.\n"
    "İLKE — şu iki koşulun İKİSİ de sağlanıyorsa keep=true:\n"
    " (1) Endpoint, müşteriye fayda sağlayan DİNAMİK/GÜNCEL veya girdiye göre hesaplanan "
    "YAPILANDIRILMIŞ veri döndürüyor: hesap sonucu (tutar/ödeme planı/getiri), ya da "
    "canlı/güncel bir değer veya tablo (kur, altın-gümüş fiyatı, kâr payı/paylaşım oranı, "
    "kredi limit/vade aralığı, mil/puan kazanım oranı vb.). Yani değeri zamanla veya "
    "girdiyle DEĞİŞEN, retrieval'daki sabit metinden elde EDİLEMEYECEK bilgi.\n"
    " (2) Kimlik/oturum GEREKTİRMEDEN, herkese açık şekilde bu sonucu veriyor.\n"
    "keep=false: izleme/analitik, reklam, çerez, config/sdk, sağlık-kontrol, arama, "
    "oturum/login/başvuru/kimlik; VEYA sadece STATİK serbest metin/HTML içerik döküyorsa "
    "(blog, kampanya anlatısı, SSS gibi — bunlar zaten arama motorunda var, dinamik değil). "
    "Kararı URL, istek ve YANIT örneğinin BİÇİMİNE bakarak, genel ilkeyle ver; "
    "belirli isimlere göre değil.\n\n"
    "METHOD: {method}\nURL: {url}\nİÇERİK-TİPİ: {ct}\n"
    "İSTEK örneği: {post}\nYANIT örneği: {resp}\n\n"
    'SADECE JSON: {{"keep": true|false, "tool": "<işlevi 2-4 kelimeyle betimle; '
    'keep=false ise boş>", "reason": "<tek cümle gerekçe>"}}')


def classify(e: dict) -> dict:
    prompt = _Q.format(
        method=e.get("method", ""), url=e.get("url", ""),
        ct=e.get("content_type", ""),
        post=(e.get("example_post") or "(yok)")[:600],
        resp=(e.get("example_resp") or "(yok)")[:600])
    d = vlm.call_json(vlm.txt_msg(prompt), max_tokens=512)
    if not d:                                  # LLM ulaşılamadı -> muhafazakâr: elde tut, işaretle
        return {"keep": bool(e.get("relevant")), "tool": "", "reason": "LLM ulaşılamadı"}
    return {"keep": bool(d.get("keep")), "tool": (d.get("tool") or "").strip(),
            "reason": (d.get("reason") or "").strip()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prune", action="store_true",
                    help="endpoints.json'ı da yalnız KEEP'lere indirge")
    args = ap.parse_args()

    src = OUT / "endpoints.json"
    cat = json.loads(src.read_text(encoding="utf-8"))
    print(f"{len(cat)} tekil endpoint eleniyor (Gemma)...")

    kept, dropped = [], []
    for i, e in enumerate(cat, 1):
        v = classify(e)
        e["_gemma"] = v
        (kept if v["keep"] else dropped).append(e)
        mark = "TUT " if v["keep"] else "sil "
        print(f"  [{i}/{len(cat)}] {mark}{v.get('tool',''):<22} {e['method']} {e['url'][:80]}")

    (OUT / "endpoints_kept.json").write_text(
        json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")

    # markdown: banka -> araç -> endpoint
    from collections import defaultdict
    by = defaultdict(list)
    for e in kept:
        by[e["slug"]].append(e)
    lines = [f"# Tutulan Canlı Araç Endpoint'leri (Gemma-elemeli)\n",
             f"Toplam {len(cat)} endpoint -> **{len(kept)} TUT**, {len(dropped)} sil\n"]
    for slug in sorted(by):
        lines.append(f"\n## {slug}\n")
        for e in by[slug]:
            g = e["_gemma"]
            lines.append(f"### {g.get('tool') or '(araç)'}")
            lines.append(f"- `{e['method']} {e['url']}`")
            if e.get("example_post"):
                lines.append(f"  - istek: `{e['example_post'][:220]}`")
            if e.get("example_resp"):
                lines.append(f"  - yanıt: `{e['example_resp'][:220]}`")
            lines.append(f"  - not: {g.get('reason','')}")
    (OUT / "endpoints_kept.md").write_text("\n".join(lines), encoding="utf-8")

    if args.prune:
        src.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"endpoints.json {len(kept)} KEEP'e indirgendi (silinen çöp gitti).")

    print(f"\nBİTTİ: {len(kept)} TUT / {len(dropped)} sil")
    print(f"  -> {OUT/'endpoints_kept.json'}\n  -> {OUT/'endpoints_kept.md'}")


if __name__ == "__main__":
    main()
