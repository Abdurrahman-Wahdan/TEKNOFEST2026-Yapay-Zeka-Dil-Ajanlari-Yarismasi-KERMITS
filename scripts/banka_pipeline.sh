#!/usr/bin/env bash
# TEK BANKA — aşama 1..4 + embed'i sırayla yürütür, TABLODAN ÖNCE DURUR.
#
# Bankalar BİRBİRİNDEN BAĞIMSIZ ilerler: bir bankanın yavaşlığı/engeli
# diğerlerini bekletmez — her banka kendi zincirini kendi hızında bitirir.
#
# kuveytturk: engellenirse (site erişilemez) bu script YORMADAN bekler —
# aşamalar arası erişim yoklaması yapar, açılınca kaldığı yerden devam eder.
# Hız profili dataprep/crawl/hiz.py::NAZIK_BANKALAR'dan gelir (1 req/sn).
#
# Kullanım: bash scripts/banka_pipeline.sh <banka>
set -uo pipefail
cd "$(dirname "$0")/.."
B="${1:?banka adi gerekli}"

# .env OTOMATİK YÜKLENMİYOR (2026-08-23): dataprep modülleri os.environ'a
# bakıyor ama hiçbir yerde load_dotenv çağrılmıyor — yani .env'e yazılan
# ayarlar bu süreçlere ULAŞMIYORDU. Kanıt: VLM_READ_TIMEOUT=900 yazıldı ama
# süreç 120 kullanmaya devam etti, ağır görsel istekleri sonsuz ReadTimeout
# döngüsüne girdi (vakifkatilim 15/706'da 18 dk takıldı).
set -a
[ -f .env ] && . ./.env
set +a
L="logs/gece/$B"; mkdir -p "$L"
say() { echo "[$(date +%H:%M:%S)] [$B] $*" | tee -a "$L/ana.log"; }

# --- site erişilebilir mi? engelliyse YORMADAN bekle ------------------------
# Yoklama isteği TEK ve hafif; aralar uzun (5 dk). Amaç siteyi zorlamak değil,
# "açıldı mı?" diye ara sıra bakmak.
erisim_bekle() {
  local url deneme=0
  url=$(python3 -c "
import sys;sys.path.insert(0,'.')
from dataprep.crawl.bank import engine
engine.load('$B'); print(engine.CONFIG['BASE'])" 2>/dev/null) || return 0
  while [ $deneme -lt 96 ]; do          # 96 x 5dk = 8 saat
    if curl -s -o /dev/null -m 20 -A "Mozilla/5.0" "$url"; then
      [ $deneme -gt 0 ] && say "site tekrar açıldı (${deneme}. yoklamada) — devam"
      return 0
    fi
    deneme=$((deneme+1))
    [ $((deneme % 6)) -eq 1 ] && say "site erişilemiyor (yoklama $deneme/96) — 5 dk sonra tekrar"
    sleep 300
  done
  say "UYARI: 8 saattir erişilemiyor — bu aşama atlanıyor"
  return 1
}

say "=== AŞAMA 1: crawl ==="
erisim_bekle && python3 -m dataprep.crawl.graph --bank "$B" > "$L/a1.log" 2>&1
say "a1 bitti: NEW=$(grep -c '  NEW ' "$L/a1.log" 2>/dev/null) $(grep -ioE 'hesab. verilemeyen URL yok|MUTABAKAT bitti: [0-9]+/[0-9]+ indirildi' "$L/a1.log" 2>/dev/null | tail -1)"

say "=== AŞAMA 2: görseller ==="
erisim_bekle && python3 -m dataprep.content "$B" --stage images > "$L/a2.log" 2>&1
say "a2 bitti (traceback: $(grep -ci traceback "$L/a2.log" 2>/dev/null))"

say "=== AŞAMA 3: PDF metni ==="
python3 -m dataprep.content "$B" --stage pdf-text > "$L/a3.log" 2>&1
say "a3 bitti (traceback: $(grep -ci traceback "$L/a3.log" 2>/dev/null))"

say "=== AŞAMA 4: sayfa temizleme + etiketler ==="
python3 -m dataprep.pages "$B" > "$L/a4.log" 2>&1
say "a4 bitti (traceback: $(grep -ci traceback "$L/a4.log" 2>/dev/null))"

say "=== EMBED: Qdrant ==="
python3 -m dataprep.embed "$B" > "$L/a5.log" 2>&1
say "embed bitti: $(grep -oE 'Toplam: [0-9]+ chunk' "$L/a5.log" 2>/dev/null | tail -1)"

say "=== BİTTİ — tablo aşaması KULLANICIYI BEKLİYOR ==="
