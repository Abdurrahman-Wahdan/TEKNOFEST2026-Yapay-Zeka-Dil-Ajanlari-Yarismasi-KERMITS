#!/usr/bin/env bash
# PDF metin/görsel çıkarımı — bankalar PARALEL, banka-içi SERİ (görsel dedup cache
# banka başına paylaşılır). HİBRİT: metin katmanı varsa sayfa-sırasıyla metin+görsel,
# taranmışsa yazılım-zoom bant-VLM.
# Kullanım:  bash dataprep/pdf/run_parallel.sh [bank1 bank2 ...]
set -u
PY=/Users/ifa/.pyenv/versions/my_venv/bin/python
cd "$(dirname "$0")/../.."          # repo kökü

DEFAULT="kuveytturk albaraka dunyakatilim emlakkatilim hayatfinans \
         vakifkatilim ziraatkatilim adilkatilim tombank turkiyefinans"
BANKS="${*:-$DEFAULT}"

echo "==== PDF PARALEL BAŞLADI ($(date +%H:%M)) ===="
for b in $BANKS; do
  "$PY" -m dataprep.pdf.extract "$b" > "/tmp/pdf_${b}.log" 2>&1 &
  echo "  başlatıldı: $b (pid $!)"
done
wait
echo "==== TÜM PDF'LER BİTTİ ($(date +%H:%M)) ===="
for b in $BANKS; do
  n=$(find "data/${b}_site/pdf_text" -name '*.md' 2>/dev/null | wc -l | tr -d ' ')
  echo "  $b -> pdf_text md=$n"
done
