"""Uçtan uca kapsama denetimi: disk -> Qdrant, banka banka.

Tablo aşamasına geçmeden önce "hiçbir sayfa kaçmadı" iddiasını KANITLAR:
  1. pipeline'ın tarayacağı işlenebilir sayfa sayısı (disk)
  2. bunların kaçı Qdrant'ta bulunabiliyor (araştırma ajanı yalnız orayı görür)
  3. point_id çakışması var mı (aynı id'ye düşen belge = üst üste yazma)

Kullanım: python3 scripts/kapsama_raporu.py
"""
from __future__ import annotations

import ast
import collections
import json
import sys
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

QDRANT = "http://localhost:6333"
KOLEKSIYON = "campaigns"


def _indeksli_urller() -> set[str]:
    urls: set[str] = set()
    off = None
    while True:
        body = {"limit": 2000, "with_payload": ["metadata"], "with_vector": False}
        if off:
            body["offset"] = off
        req = urllib.request.Request(
            f"{QDRANT}/collections/{KOLEKSIYON}/points/scroll",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        r = json.load(urllib.request.urlopen(req, timeout=120))["result"]
        for p in r["points"]:
            m = p["payload"].get("metadata")
            if isinstance(m, str):
                try:
                    m = ast.literal_eval(m)
                except Exception:
                    continue
            urls.add((m or {}).get("url", ""))
        off = r.get("next_page_offset")
        if not off:
            break
    return urls


def main() -> None:
    from dataprep.compare import pipeline as P
    import dataprep.embed as E

    indeksli = _indeksli_urller()
    # Kuveytturk'ün bazı URL'leri sonda '-' taşıyabiliyor; kıyas normalize edilir.
    norm = {u.rstrip("-") for u in indeksli}
    print(f"Qdrant '{KOLEKSIYON}': {len(indeksli)} benzersiz URL\n")

    print(f"{'BANKA':16}{'SAYFA':>7}{'QDRANTTA':>10}{'EKSİK':>7}  DURUM")
    toplam_eksik = 0
    eksik_detay: dict[str, list[str]] = {}
    for b in P.all_banks():
        sayfa = 0
        eksik: list[str] = []
        for _i, _t, url, _body, skip in P._pages(b, None):
            if skip:
                continue
            sayfa += 1
            if url.rstrip("-") not in norm:
                eksik.append(url)
        toplam_eksik += len(eksik)
        if eksik:
            eksik_detay[b] = eksik
        durum = "✅" if not eksik else "❌"
        print(f"{b:16}{sayfa:>7}{sayfa - len(eksik):>10}{len(eksik):>7}  {durum}")
    print(f"\nTOPLAM EKSİK: {toplam_eksik}")
    for b, v in eksik_detay.items():
        print(f"\n  {b} ({len(v)}):")
        for u in v[:10]:
            print(f"    {u[:110]}")

    # --- point_id çakışması -------------------------------------------------
    print("\n--- point_id çakışma denetimi ---")
    cakisma = 0
    for b in P.all_banks():
        ids: collections.Counter = collections.Counter()
        for i, d in enumerate(E.iter_docs(b)):
            m = d.metadata
            k = (f"{b}:{m.get('url','')}:{m.get('type','')}:"
                 f"{m.get('gorsel_kaynak','')}:{m.get('gorsel_index','')}:"
                 f"{m.get('chunk_index', i)}")
            ids[str(uuid.uuid5(uuid.NAMESPACE_URL, k))] += 1
        d = sum(c - 1 for c in ids.values() if c > 1)
        if d:
            print(f"  {b}: ÇAKIŞMA {d}")
        cakisma += d
    print(f"  toplam çakışma: {cakisma}")

    print("\n" + ("HAZIR: tablo aşamasına geçilebilir ✅"
                  if toplam_eksik == 0 and cakisma == 0
                  else "EKSİK VAR — tablo aşamasından ÖNCE giderilmeli ❌"))


if __name__ == "__main__":
    main()
