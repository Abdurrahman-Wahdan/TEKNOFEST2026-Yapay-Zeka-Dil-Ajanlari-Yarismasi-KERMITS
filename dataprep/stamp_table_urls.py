"""Qdrant 'compare_tables' koleksiyonundaki her tabloya UI adresini `ui_url` damgala.

Amaç: ajan bir karşılaştırma tablosundan söz ettiğinde, kullanıcıya o tablonun
BİZİM arayüzümüzdeki adresini de verebilsin — kullanıcı linke basınca zaten
hazır duran tabloya düşsün. Bunun için tablonun adresi, tablonun kendi
point'inin payload'ında durmalı: ajan tabloyu bulduğu anda linki de elinde
olur, ayrıca bir çözümleme adımı gerekmez.

Yeniden embed YOK. Vektörler ve metin olduğu gibi kalır, yalnızca payload'a tek
bir alan eklenir (`client.set_payload`) — `dataprep/stamp_campaign_end.py` ile
aynı desen ve aynı sebeple: eklenen bilgi metnin anlamını değiştirmiyor, o
yüzden embedding'i yeniden hesaplamak boşa iş olur.

Tekrar çalıştırılabilir: tablo havuzu (`_tables/*.json`) yenilendikten sonra bu
komut yeniden koşulur, değişmemiş olanlar atlanır ve yalnızca farklı olanlar
yazılır. Havuzda olmayan ya da koleksiyonda bulunmayan tablolar sessizce
geçilmez, raporlanır.

ADRES SİTE-GÖRELİDİR (`/tr/kampanyalar?tablo=...`), mutlak değil. İki sebep:
koleksiyon bir derleme çıktısıdır ve içine gömülen bir alan adı ilk taşımada
bayatlar; ayrıca `UI/src/components/chat/AgentMarkdown.tsx` `http` ile
BAŞLAYAN linkleri yeni sekmede açıyor, görelileri uygulama içinde — yani göreli
adres kullanıcıyı sohbetten koparmadan tabloya götürür. Mutlak adres gerekiyorsa
`--base-url` ile öne eklenir.

Kullanım:
    python -m dataprep.stamp_table_urls               # damgala
    python -m dataprep.stamp_table_urls --dry-run     # ne yazacağını göster
"""
from __future__ import annotations

import argparse
import logging

from api import compare_tables_pool as pool
from api.table_links import ui_url
from corpus.tables import TABLES_COLLECTION, table_point_id
from vector_stores.client import get_qdrant_client

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("dataprep.stamp_table_urls")

# Damgalanan alan. `campaigns` koleksiyonundaki `url` BANKANIN kaynak sayfasıdır;
# bu ondan ayrı bir şey — bizim arayüzümüzdeki sayfa. İsimleri karışırsa okuyan
# taraf yanlış linki verir, o yüzden ayrı ve açık: `ui_url`.
FIELD = "ui_url"

# Adresin nasıl yazıldığı — parametre adı, kategori->yol eşlemesi, /tr öneki ve
# ters yönü okuyan `parse_ui_url` — `api/table_links.py`'de, TEK yerde. Burada da
# bir kopyası dururken `api/agent.py` üçüncüsünü yazmak üzereydi.
def _nfc(value: str) -> str:
    """Türkçe id'ler diskte NFD, dosyanın İÇİNDE NFC olabiliyor (bkz.
    `compare_tables_pool.load_table`). Karşılaştırma da, uuid5 türetmesi de
    bayta duyarlı olduğu için her id tek biçime çekilir."""
    return unicodedata.normalize("NFC", value or "")


def _collection_ids(client) -> dict[str, list]:
    """Koleksiyondaki `payload.id` -> point id'leri.

    Point id'yi türetmek yerine okuyoruz: koleksiyonda gerçekten NE varsa o
    damgalanır. Türetilen uuid5 ile karşılaştırması ayrıca yapılır, böylece
    ikisinin ayrıştığı gün sessizce yanlış point'e yazmak yerine haber verir.
    Liste, çünkü aynı id iki point'e yazılmış olabilir — o da bir bulgudur.
    """
    found: dict[str, list] = {}
    offset = None
    while True:
        points, offset = client.scroll(
            TABLES_COLLECTION, limit=1000, with_payload=[FIELD, "id"],
            with_vectors=False, offset=offset)
        for pt in points:
            payload = pt.payload or {}
            table_id = _nfc(payload.get("id") or "")
            if not table_id:
                log.warning("payload'ında id olmayan point: %s", pt.id)
                continue
            found.setdefault(table_id, []).append((pt.id, payload.get(FIELD)))
        if offset is None:
            return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Hiçbir şey yazma, yalnızca ne değişeceğini raporla.")
    parser.add_argument("--base-url", default="",
                        help="Adresin önüne eklenecek kök (ör. https://app.example.com). "
                             "Boş bırakılırsa adres site-göreli kalır — varsayılan budur.")
    args = parser.parse_args(argv)

    client = get_qdrant_client()
    if not client.collection_exists(TABLES_COLLECTION):
        log.error("'%s' koleksiyonu yok. Önce tablo havuzu indekslenmeli "
                  "(dataprep.compare -> corpus.tables.index_table).", TABLES_COLLECTION)
        return 1

    tables = pool.all_tables()
    in_collection = _collection_ids(client)
    log.info("havuz: %d tablo · koleksiyon: %d tablo", len(tables), len(in_collection))

    written = unchanged = 0
    uncategorised: list[str] = []
    absent: list[str] = []
    duplicated: list[str] = []
    id_drift: list[str] = []

    for table in tables:
        table_id = _nfc(table.get("id") or "")
        if not table_id:
            continue
        url = ui_url(table_id, table.get("category") or "", args.base_url)
        if url is None:
            uncategorised.append(table_id)
            continue
        points = in_collection.pop(table_id, None)
        if not points:
            absent.append(table_id)
            continue
        if len(points) > 1:
            duplicated.append(table_id)
        if table_point_id(table_id) not in {str(pid) for pid, _ in points}:
            id_drift.append(table_id)
        stale = [pid for pid, current in points if current != url]
        if not stale:
            unchanged += len(points)
            continue
        if not args.dry_run:
            client.set_payload(TABLES_COLLECTION, payload={FIELD: url}, points=stale, wait=True)
        written += len(stale)

    verb = "yazılacak" if args.dry_run else "yazıldı"
    log.info("BİTTİ: %d point %s, %d zaten doğru.", written, verb, unchanged)

    # Sessiz geçilmeyecek olanlar. Hiçbiri hata değil, hepsi "burada bir şey
    # kaymış" demek — ve bir tanesi bile fark edilmezse ajan o tabloya link
    # veremez ya da yanlış link verir.
    for label, ids in (("kategorisi tanınmadı", uncategorised),
                       ("koleksiyonda yok (indekslenmemiş)", absent),
                       ("aynı id birden çok point'te", duplicated),
                       ("point id'si uuid5 türetmesiyle uyuşmuyor", id_drift)):
        if ids:
            log.warning("%d tablo — %s: %s%s", len(ids), label, ", ".join(ids[:5]),
                        " …" if len(ids) > 5 else "")
    if in_collection:
        log.warning("%d tablo koleksiyonda var ama havuzda yok (silinmiş olabilir): %s%s",
                    len(in_collection), ", ".join(list(in_collection)[:5]),
                    " …" if len(in_collection) > 5 else "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
