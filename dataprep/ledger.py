"""İŞLEME GÜNLÜĞÜ (ledger) — sayfa + görsel + PDF için öğe-bazlı, kalıcı denetim izi.

Amaç: her gün (ya da kullanıcı isteğinde) yeniden tarama yapılırken HER öğeye ne
yapıldığının açıklanabilmesi:
  * sayfa   -> NEW/CHANGED/SAME/FAIL/EMPTY + kaydedildi/atlandı
  * görsel  -> içerik / dekoratif(gereksiz) / erişilemedi / indirilemedi + gerekçe
  * pdf     -> related/unrelated(gereksiz) + gerekçe, boş/işlendi

Her banka için tek dosya: data/<bank>_site/_processing_log.jsonl (append-only JSONL).
Bir satır = bir öğe-olayı; günler biriktikçe tam geçmiş oluşur. Thread-safe (görsel
aşaması banka-içi paralel çalışır).

Hızlı özet için: python -m dataprep.ledger <bank>   (son koşunun dağılımını yazar)
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

_LOCK = threading.Lock()


class Ledger:
    """Append-only JSONL işleme günlüğü. record(...) çağrısı thread-safe'tir."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, stage: str, url: str, *, status: str = "", decision: str = "",
               reason: str = "", **extra) -> None:
        """Bir öğe-olayını yaz. stage: page|image|pdf. decision: saved/skip/content/
        decorative/unreachable/download_fail/related/unrelated/empty ..."""
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "stage": stage, "url": url, "status": status,
               "decision": decision, "reason": reason[:8000] if reason else ""}
        if extra:
            rec.update(extra)
        line = json.dumps(rec, ensure_ascii=False)
        with _LOCK:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")


def summarize(jsonl_path: Path) -> dict:
    """Günlükteki SON kayıtları öğe (url+stage) bazında toplayıp karar dağılımını çıkarır."""
    latest: dict[tuple, dict] = {}
    if not Path(jsonl_path).exists():
        return {}
    for line in Path(jsonl_path).read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        latest[(r.get("stage"), r.get("url"))] = r
    from collections import Counter
    by_stage: dict[str, Counter] = {}
    for (stage, _), r in latest.items():
        by_stage.setdefault(stage, Counter())[r.get("decision") or r.get("status") or "?"] += 1
    return {s: dict(c) for s, c in by_stage.items()}


def main():
    import argparse
    import glob
    ap = argparse.ArgumentParser(description="İşleme günlüğü özeti")
    ap.add_argument("banks", nargs="*")
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[1] / "data"
    banks = args.banks or sorted(Path(d).name[:-5] for d in glob.glob(str(root / "*_site")))
    for b in banks:
        s = summarize(root / f"{b}_site" / "_processing_log.jsonl")
        print(f"\n=== {b} ===")
        for stage, dist in s.items():
            print(f"  {stage}: {dist}")


if __name__ == "__main__":
    main()
