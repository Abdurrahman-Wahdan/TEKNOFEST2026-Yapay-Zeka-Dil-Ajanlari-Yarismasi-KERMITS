#!/usr/bin/env bash
# Bankaları PARALEL tarar: her banka ayrı süreçte AYNI ANDA (banka-içi sıralı).
# LLM server eşzamanlılığı kaldırıyor (test edildi: 10 eşzamanlı istek sorunsuz).
# Kullanım:  bash dataprep/crawl/run_parallel.sh [bank1 bank2 ...]
set -u
PY=/Users/ifa/.pyenv/versions/my_venv/bin/python
cd "$(dirname "$0")/../.."          # repo kökü

DEFAULT="kuveytturk albaraka dunyakatilim emlakkatilim hayatfinans \
         vakifkatilim ziraatkatilim adilkatilim tombank turkiyefinans"
BANKS="${*:-$DEFAULT}"

echo "==== PARALEL BAŞLADI ($(date +%H:%M)) ===="
pids=()
for b in $BANKS; do
  extra="--render"                   # EN GENİŞ: tüm bankalar render (JS-nav linkleri de yakalanır)
  "$PY" -m dataprep.crawl.graph --bank "$b" --max-retries 4 $extra \
        > "/tmp/crawl_${b}.log" 2>&1 &
  echo "  başlatıldı: $b (pid $!) $extra"
  pids+=($!)
done

wait                                 # TÜM bankalar bitene kadar bekle

echo "==== TÜM BANKALAR BİTTİ ($(date +%H:%M)) ===="
for b in $BANKS; do
  md=$(find "data/${b}_site" -name '*.md' 2>/dev/null | grep -v pdfs | wc -l | tr -d ' ')
  pdf=$(find "data/${b}_site/pdfs" -name '*.pdf' 2>/dev/null | wc -l | tr -d ' ')
  echo "  $b -> md=$md pdf=$pdf"
done
echo "==== HEPSİ BİTTİ ($(date +%H:%M)) ===="
