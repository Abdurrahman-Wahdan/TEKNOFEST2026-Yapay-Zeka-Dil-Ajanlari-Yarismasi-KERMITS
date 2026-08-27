#!/usr/bin/env bash
# TEK BANKA — aşama 1'den EMBED'e kadar SERİ, tek koşu.
#
# NEDEN SERİ: 10 banka paralelken (a) hangi bankanın hangi hatayı
# aldığı karışıyor, (b) her süreç NET_SEM'den küçük bir pay alıyor ve
# semafor kuyruğu tıkanıyor, (c) bir düzeltme için hepsini yeniden
# başlatmak tüm ilerlemeyi harcıyordu. Tek bankada bunların hiçbiri yok:
# tam eşzamanlılık bütçesi (25) tek sürecin olur, hata net görünür.
#
# TABLO AŞAMASI BURADA YOK — bilerek. Tablo kurulurken 10 BANKAYA fan-out
# yapılıyor (araştırmacı ajanlar her bankanın Qdrant verisini arar), yani
# TÜM bankalar embed'i bitirmeden tablo kurmak eksik veriyle tablo üretir.
# Tablo, hepsi bittikten sonra TEK SEFERDE ve kullanıcı onayıyla çalışır.
#
# Kullanım: bash scripts/tek_banka.sh <banka>
set -uo pipefail
cd "$(dirname "$0")/.."
B="${1:?banka adi gerekli}"
set -a; [ -f .env ] && . ./.env; set +a
L="logs/seri/$B"; mkdir -p "$L"
say() { echo "[$(date '+%H:%M:%S')] [$B] $*" | tee -a "$L/ana.log"; }

basla=$(date +%s)
# BİTMİŞ AŞAMALARI ATLA — her aşama için diskteki gerçeğe bakılır; iş yoksa
# aşama hiç çalıştırılmaz. Aşamalar zaten artımlıdır, yani veri kaybı olmaz —
# ama her tur crawl/görsel/PDF'i yeniden taramak dakikalar harcar.
eval "$(python3 scripts/asama_durumu.py "$B" 2>/dev/null)"
say "durum: A1=${A1:-1} A2=${A2:-1} A3=${A3:-1} A4=${A4:-1} (1=iş var)"
# HER AŞAMA KENDİ İÇİNDE TAMAMLANANA KADAR TEKRARLANIR — sonraki aşamaya
# (ve sonraki bankaya) YARIM geçilmez (kullanıcı kararı 2026-08-23).
say "=== AŞAMA 1: crawl (1 req/sn) ==="
# KALICI BİTİŞ DAMGASI: a1.log her koşuda ÜZERİNE yazılıyor, bu yüzden
# "log'da BİTTİ var mı" ölçütü bir sonraki turda kendi kanıtını siliyordu
# (ölçüldü: crawl bitmiş olduğu halde tekrar koşuyordu).
# Damga data/<banka>_site/_a1_bitti.stamp — crawl temiz bittiğinde yazılır.
STAMP="data/${B}_site/_a1_bitti.stamp"
if [ "${A1:-1}" = "0" ] || [ -f "$STAMP" ]; then
  say "a1 ZATEN TAMAM — atlanıyor ($([ -f "$STAMP" ] && echo damga || echo denetim))"
else
for tur in 1 2 3; do
  python3 -m dataprep.crawl.graph --bank "$B" > "$L/a1.log" 2>&1
  # Bitiş ölçütü: koşu "=== BİTTİ" ile kapandı VE mutabakat hesabı temiz.
  bitti=$(grep -c '=== BİTTİ' "$L/a1.log" 2>/dev/null)
  temiz=$(grep -ci 'hesab. verilemeyen URL yok' "$L/a1.log" 2>/dev/null)
  say "a1 tur$tur: NEW=$(grep -c '  NEW ' "$L/a1.log" 2>/dev/null) $(grep -ioE 'hesab. verilemeyen URL yok|MUTABAKAT bitti: [0-9]+/[0-9]+ indirildi' "$L/a1.log" 2>/dev/null|tail -1)"
  if [ "${bitti:-0}" != "0" ]; then
    # Kaçak YOKSA damgala; kaçak varsa damgalama (sonraki tur yine denesin).
    [ "${temiz:-0}" != "0" ] && date '+%Y-%m-%dT%H:%M:%S' > "$STAMP"
    break
  fi
  say "a1 tur$tur YARIM kaldı (koşu kapanmadı) — tekrar"
done
fi

say "=== AŞAMA 2: görseller ==="
if [ "${A2:-1}" = "0" ]; then say "a2 ZATEN TAMAM — atlanıyor"; else
for tur in 1 2 3; do
  python3 -m dataprep.content "$B" --stage images > "$L/a2.log" 2>&1
  kalan=$(grep -ohE '[0-9]+ işlenecek' "$L/a2.log" 2>/dev/null|tail -1|grep -oE '[0-9]+')
  say "a2 tur$tur: $(grep -ohE '[0-9]+/[0-9]+ URL işlendi' "$L/a2.log" 2>/dev/null|tail -1) (kalan=${kalan:-0})"
  [ "${kalan:-0}" = "0" ] && break
done
fi

say "=== AŞAMA 3: PDF metni ==="
if [ "${A3:-1}" = "0" ]; then say "a3 ZATEN TAMAM — atlanıyor"; else
for tur in 1 2 3; do
  python3 -m dataprep.content "$B" --stage pdf-text > "$L/a3.log" 2>&1
  kalan=$(grep -ohE '[0-9]+ işlenecek' "$L/a3.log" 2>/dev/null|tail -1|grep -oE '[0-9]+')
  say "a3 tur$tur: $(grep -ohE '[0-9]+/[0-9]+ PDF işlendi' "$L/a3.log" 2>/dev/null|tail -1) (erken eleme: $(grep -c 'dikişe HİÇ BAŞLANMIYOR' "$L/a3.log" 2>/dev/null), kalan=${kalan:-0})"
  [ "${kalan:-0}" = "0" ] && break
done
fi

# AŞAMA 4 — YARIM KALIRSA TEKRARLA. Aşama 4 asılıp öldürülürse script
# sonraki aşamaya geçiyordu ve sayfaların bir kısmı TEMİZLENMEDEN kalıyordu
# (ölçüldü: 2273 sayfanın yalnız 700'ü işlendi). Artık "yapılacak 0"
# olana kadar tekrarlanır; artımlı olduğu için her tur kaldığı yerden devam
# eder, en fazla 5 tur denenir (sonsuz döngüye karşı).
say "=== AŞAMA 4: sayfa temizleme + etiketler ==="
# TUR SAYISI 12: her tur artımlı ilerler (önbellek 10 sayfada bir diske
# yazılır), tünel kesintisi turu böldüğünde kaldığı yerden devam eder.
for tur in 1 2 3 4 5 6 7 8 9 10 11 12; do
  python3 -m dataprep.pages "$B" > "$L/a4_t${tur}.log" 2>&1
  cp "$L/a4_t${tur}.log" "$L/a4.log"
  kalan=$(grep -ohE '[0-9]+ yapılacak' "$L/a4_t${tur}.log" 2>/dev/null|tail -1|grep -oE '[0-9]+')
  say "a4 tur$tur: $(grep -ohE '[0-9]+ sayfa \(.*yapılacak' "$L/a4_t${tur}.log" 2>/dev/null|tail -1)"
  [ "${kalan:-0}" = "0" ] && break
  say "a4 tur$tur yarım kaldı (kalan=$kalan) — tekrar deneniyor"
done

say "=== EMBED: Qdrant ==="
for tur in 1 2 3; do
  python3 -m dataprep.embed "$B" > "$L/a5.log" 2>&1
  # "Yüklenecek: N" satırındaki N, o turda EKSİK olan chunk sayısıdır.
  kalan=$(grep -ohE 'Yüklenecek: [0-9]+' "$L/a5.log" 2>/dev/null|tail -1|grep -oE '[0-9]+')
  say "embed tur$tur: $(grep -oE 'Toplam: [0-9]+ chunk' "$L/a5.log" 2>/dev/null|tail -1) (kalan=${kalan:-0})"
  [ "${kalan:-0}" = "0" ] && break
  say "embed tur$tur yarım kaldı (kalan=$kalan) — tekrar"
done

say "=== DENETİM ==="
python3 -m dataprep.verify "$B" > "$L/verify.log" 2>&1
say "$(grep -A1 'AÇIKLANAMAYAN' "$L/verify.log" 2>/dev/null|tail -1)"

sure=$(( ($(date +%s) - basla) / 60 ))
say "=== BİTTİ (${sure} dk) ==="
