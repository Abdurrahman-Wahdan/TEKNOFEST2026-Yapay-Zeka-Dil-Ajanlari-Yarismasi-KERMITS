#!/usr/bin/env bash
# Web sayfa GÖRSELLERİ -> item-VLM (dekoratif eleme + içerik çıkarımı).
# Bankalar PARALEL, banka-içi SERİ; görsel hash-dedup cache banka başına paylaşılır.
# Kullanım:  bash dataprep/run_images.sh [bank1 bank2 ...]
set -u
PY=/Users/ifa/.pyenv/versions/my_venv/bin/python
cd "$(dirname "$0")/.."             # repo kökü

DEFAULT="kuveytturk albaraka dunyakatilim emlakkatilim hayatfinans \
         vakifkatilim ziraatkatilim adilkatilim tombank turkiyefinans"
BANKS="${*:-$DEFAULT}"

echo "==== GÖRSEL PARALEL BAŞLADI ($(date +%H:%M)) ===="
for b in $BANKS; do
  "$PY" -m dataprep.images "$b" > "/tmp/img_${b}.log" 2>&1 &
  echo "  başlatıldı: $b (pid $!)"
done
wait
echo "==== TÜM GÖRSELLER BİTTİ ($(date +%H:%M)) ===="
for b in $BANKS; do
  n=$(find "data/${b}_site/image_text" -name '*.md' 2>/dev/null | wc -l | tr -d ' ')
  echo "  $b -> image_text md=$n"
done
