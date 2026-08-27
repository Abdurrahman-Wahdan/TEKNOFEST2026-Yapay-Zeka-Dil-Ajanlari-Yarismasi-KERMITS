#!/usr/bin/env bash
# TABLO SONRASI BAKIM — tablo üretimi bittikten sonra çalışan KALICI akış.
#
#   dedup -> denetim -> tarih -> expire -> sunulmuyor
#
# Her koşudan ÖNCE tabloların yedeğini alır (data/_tables_yedek_<ts>), böylece
# bir adım beklenmedik bir şey yaparsa geri dönülebilir. ARTIMLI değildir:
# her çalıştırmada tüm tablolar yeniden işlenir — bu kasıtlı, çünkü kaynak
# sayfalar güncellendiğinde tablonun da güncellenmesi gerekir.
#
# Kullanım:
#   bash scripts/tablo_son_islem.sh                 # tüm adımlar
#   bash scripts/tablo_son_islem.sh --adim tarih    # tek adım
#   bash scripts/tablo_son_islem.sh --atla dedup    # bir adımı atla
#   bash scripts/tablo_son_islem.sh --kuru          # denetimi yazmadan dene
set -uo pipefail
cd "$(dirname "$0")/.."
set -a; [ -f .env ] && . ./.env; set +a

TS=$(date +%Y%m%d_%H%M%S)
L=logs/son_islem; mkdir -p "$L"
LOG="$L/pipeline_${TS}.log"

# Zaten çalışan bir koşu varsa ikincisini başlatma: aynı JSON dosyalarına
# iki süreç yazarsa tablolar bozulur.
if pgrep -f "dataprep.compare.son_islem" >/dev/null 2>&1; then
  echo "!! son_islem zaten çalışıyor (PID: $(pgrep -f 'dataprep.compare.son_islem' | tr '\n' ' '))"
  echo "   Aynı dosyalara iki süreç yazamaz. Önce onu bekleyin ya da durdurun."
  exit 1
fi

YEDEK="data/_tables_yedek_${TS}"
if [ -d data/_tables ]; then
  cp -R data/_tables "$YEDEK"
  echo "[$(date '+%H:%M:%S')] yedek: $YEDEK ($(ls "$YEDEK"/[!_]*.json 2>/dev/null | wc -l | tr -d ' ') tablo)"
fi

# Qdrant tablo koleksiyonunun anlık görüntüsü (dedup orayı da değiştirir)
curl -s -X POST "http://localhost:6333/collections/compare_tables/snapshots" \
     -H 'Content-Type: application/json' >/dev/null 2>&1 \
  && echo "[$(date '+%H:%M:%S')] qdrant snapshot alındı" \
  || echo "[$(date '+%H:%M:%S')] uyarı: qdrant snapshot alınamadı (koşu yine de sürüyor)"

echo "[$(date '+%H:%M:%S')] başlıyor -> $LOG"
echo "$LOG" > .son_islem_log
nohup python3 -u -m dataprep.compare.son_islem "$@" > "$LOG" 2>&1 &
PID=$!
echo "[$(date '+%H:%M:%S')] PID=$PID"
echo "   izlemek için: tail -f $LOG | grep -vE 'HTTP Request|Retrying'"
