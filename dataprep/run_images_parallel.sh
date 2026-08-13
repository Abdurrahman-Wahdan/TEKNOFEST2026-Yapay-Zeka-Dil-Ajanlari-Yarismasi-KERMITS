#!/usr/bin/env bash
# Sayfa GÖRSELLERİ -> item-VLM. BANKALAR ARASI PARALEL, banka-içi düşük eşzamanlılık.
# Neden: indirme limiti PER banka-sitesi. Paralel bankalar yükü 10 domain'e yayar (her
# siteye az = throttle yok), toplam throughput yüksek. VLM açık bağlantısı: banka × POOL < 100.
#   IMG_WORKERS=10 (site başına indirme), VLM_POOL=9 (10 banka × 9 = 90 < 100).
# Kullanım: bash dataprep/run_images_parallel.sh [bank1 bank2 ...]
set -u
PY=/Users/ifa/.pyenv/versions/my_venv/bin/python
cd "$(dirname "$0")/.."

DEFAULT="turkiyefinans kuveytturk vakifkatilim ziraatkatilim albaraka \
         dunyakatilim emlakkatilim hayatfinans tombank adilkatilim"
BANKS="${*:-$DEFAULT}"
N=$(echo $BANKS | wc -w | tr -d ' ')
POOL=$(( 95 / N )); [ "$POOL" -lt 1 ] && POOL=1     # toplam açık bağlantı < 100

echo "==== GÖRSEL PARALEL BAŞLADI ($(date +%H:%M)) — $N banka, VLM_POOL=$POOL/banka, IMG_WORKERS=${IMG_WORKERS:-10} ===="
for b in $BANKS; do
  VLM_POOL="$POOL" IMG_WORKERS="${IMG_WORKERS:-6}" \
    "$PY" -m dataprep.images "$b" > "/tmp/img_${b}.log" 2>&1 &
  echo "  başlatıldı: $b (pid $!)"
done
wait
echo "==== GÖRSEL BİTTİ ($(date +%H:%M)) ===="
for b in $BANKS; do
  n=$(find "data/${b}_site/image_text" -name '*.md' 2>/dev/null | wc -l | tr -d ' ')
  echo "  $b -> image_text=$n"
done
