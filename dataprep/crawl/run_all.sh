#!/usr/bin/env bash
# Tüm bankaları sırayla, her birini ayrı süreçte tarar (motor durumu izole).
# JS-render (SPA) bankalara otomatik --render + uygun disco-cap uygulanır.
# Kullanım:  bash dataprep/crawl/run_all.sh [bank1 bank2 ...]   (argümansız: 10 banka)
set -u
PY=/Users/ifa/.pyenv/versions/my_venv/bin/python
cd "$(dirname "$0")/../.."          # repo kökü

DEFAULT="kuveytturk albaraka dunyakatilim emlakkatilim hayatfinans \
         vakifkatilim ziraatkatilim adilkatilim tombank turkiyefinans"
BANKS="${*:-$DEFAULT}"

for b in $BANKS; do
  extra=""
  case "$b" in
    adilkatilim)   extra="--render" ;;
    tombank)       extra="--render" ;;
    turkiyefinans) extra="--render" ;;
  esac
  echo "==================== $b $extra ($(date +%H:%M)) ===================="
  "$PY" -m dataprep.crawl.graph --bank "$b" --max-retries 4 $extra \
        > "/tmp/crawl_${b}.log" 2>&1
  md=$(find "data/${b}_site" -name '*.md' 2>/dev/null | grep -v pdfs | wc -l | tr -d ' ')
  pdf=$(find "data/${b}_site/pdfs" -name '*.pdf' 2>/dev/null | wc -l | tr -d ' ')
  echo "  $b BİTTİ -> md=$md pdf=$pdf  ($(date +%H:%M))"
done
echo "==================== HEPSİ BİTTİ ($(date +%H:%M)) ===================="
