#!/usr/bin/env bash
# AŞAMA 2->3->4->EMBED (aşama 1 ATLANIR — katalog zaten dolu).
# Kullanıcı haklı olarak uyardı: her düzeltmede tüm süreçleri sıfırlamak
# ilerlemeyi boşa harcadı ve aşama 1 defalarca tekrarlandı. Katalog doluyken
# aşama 1'i yeniden koşturmanın getirisi yok (NEW=0 ölçüldü).
set -uo pipefail
cd "$(dirname "$0")/.."
B="${1:?banka}"
set -a; [ -f .env ] && . ./.env; set +a
L="logs/gece/$B"; mkdir -p "$L"
say() { echo "[$(date +%H:%M:%S)] [$B] $*" | tee -a "$L/ana.log"; }
say "=== AŞAMA 2: görseller ==="
python3 -m dataprep.content "$B" --stage images > "$L/a2.log" 2>&1
say "a2 bitti"
say "=== AŞAMA 3: PDF metni ==="
python3 -m dataprep.content "$B" --stage pdf-text > "$L/a3.log" 2>&1
say "a3 bitti"
say "=== AŞAMA 4: sayfa temizleme ==="
python3 -m dataprep.pages "$B" > "$L/a4.log" 2>&1
say "a4 bitti"
say "=== EMBED ==="
python3 -m dataprep.embed "$B" > "$L/a5.log" 2>&1
say "embed bitti: $(grep -oE 'Toplam: [0-9]+ chunk' "$L/a5.log" 2>/dev/null|tail -1)"
say "=== BİTTİ — tablo aşaması KULLANICIYI BEKLİYOR ==="
