"""AŞAMA 3 (PDF metni) ve AŞAMA 4 (sayfa metni) için ORTAK temizleme promptu.

Aşama 3 ve 4 birebir aynı süreçtir — yalnız girdi türü (PDF / sayfa) farklıdır.
Bu yüzden İKİSİ DE BU DOSYADAN okur; girdi farkı çağrı tarafında kalır,
TALİMAT tektir.

NEDEN AYRI BİR MODÜL: prompt parçaları eskiden `content.py` (PDF) ve
`pages.py` (sayfa) içinde AYRI AYRI tanımlıydı ve zamanla birbirinden
ayrışmışlardı (ölçüldü: _GOAL 791 vs 1010 karakter, _DATE_RULE
448 vs 334, _RELEVANCE_RULE 300 vs 401). Aynı işi yapmaları gerekirken FARKLI
talimatlarla çalışıyorlardı — PDF'ten çıkan etiketlerle sayfadan çıkanlar
tutarsız olabiliyordu. Tek kaynak bu ayrışmayı yapısal olarak imkânsız kılar.

BİRLEŞTİRME İLKESİ: iki sürümün BİRLEŞİMİ alındı, kesişimi değil — hiçbir
kural düşürülmedi. İki tarafta da olan kurallar bir kez yazıldı; yalnız
birinde olanlar (ör. sayfa tarafının 'Mevduat -> Katılma/Cari Hesap'
terminolojisi ve 'her bölüm kendi içinde anlaşılır' chunk kuralı; PDF
tarafının 'suresi_gecmis' çıkarımı ve 'kıyaslanabilirlik' vurgusu) korundu.
"""
from __future__ import annotations

GOAL = (
    "Sen bir KATILIM BANKASI veri seti hazırlıyorsun. Aşağıdaki HAM metin bir "
    "web sayfasından ya da PDF'ten geliyor. Görevin bir FİLTRELEME görevidir, "
    "ÖZETLEME DEĞİLDİR.\n"
    "- KORU: müşterinin işine yarayacak katılım bankacılığı ürün/kampanya/"
    "hizmet bilgisi (koşul, oran, kâr payı, ücret, tutar, vade, tarih, "
    "avantaj, SSS) EKSİKSİZ kalır — kısaltma, özetleme, atlama YAPMA; ne kadar "
    "uzun olursa olsun tüm detayı aynen aktar.\n"
    "- AT: nav menüsü, footer, duyuru, çerez bildirimi, sosyal medya, "
    "iletişim, sayfa numarası gibi TEKRARLAYAN/GEREKSİZ öğeler.\n"
    "- VERİYİ DEĞİŞTİRME: tüm SAYILAR, ORAN/ÜCRET/TUTAR, tarihler ve TABLOLAR "
    "AYNEN korunur (tablolar markdown tablo olarak); tek bir rakamı bile "
    "değiştirme, uydurma ya da atma.\n"
    "- TERMİNOLOJİ: kurum bir KATILIM BANKASI'dır (faizsiz bankacılık). "
    "'Kredi' yerine 'Finansman', 'Faiz' yerine 'Kâr Payı/Kâr Oranı', 'Mevduat' "
    "yerine 'Katılma/Cari Hesap' kullan; kaynak metin konvansiyonel terim "
    "kullansa bile SEN çevir ve genel 'banka/kredi' ifadelerine İNDİRGEME. "
    "Tek istisna: 'kredi kartı' yerleşik bir ürün adıdır, olduğu gibi kalır.\n"
    "- BİÇİM: çıktı LLM-friendly markdown — net başlıklar (##/###), kısa ve öz "
    "paragraflar, HER BÖLÜM KENDİ İÇİNDE ANLAŞILIR olsun (metin sonradan "
    "chunk'lara bölününce bütünlük bozulmasın).\n"
    "- Gerçek ürün/kampanya içeriği yoksa content'i BOŞ bırak."
)

DATE_RULE = (
    "\n\nAYRICA — GEÇERLİLİK/KAMPANYA TARİHİ: Metinde bir kampanya/teklif "
    "başlangıç ve/veya bitiş tarihi GÖRÜRSEN bildir (biri, ikisi ya da hiçbiri "
    "olabilir). Aramak için uğraşma; açıkça yazıyorsa al, yoksa boş bırak. "
    "Birden çok bitiş tarihi varsa EN GEÇ olanı ver. Tarih UYDURMA."
    "\n\nAYRICA — DURUM: bu içerik BUGÜN hâlâ geçerli mi? BUGÜNÜN TARİHİ: "
    "{today}. Kararı SEN ver ve 'durum' alanına yaz: hâlâ geçerliyse "
    "'gecerli', süresi geçmişse 'suresi_gecmis', karar veremiyorsan "
    "'bilinmiyor'. Bulduğun tarihleri, göreceli zaman ifadelerini (ör. 'bu ay "
    "sonuna kadar', 'yıl sonu') ve metindeki açık ifadeleri (ör. 'kampanya "
    "sona ermiştir') birlikte değerlendir. Emin değilsen 'bilinmiyor' yaz — "
    "belirsizlikte içeriği elettirme."
)

RELEVANCE_RULE = (
    "\n\nBUGÜNÜN TARİHİ: {today}"
    "\n\nAYRICA — İÇERİK DEĞERLENDİRMESİ: Verilen URL'den, dosya adından, üst dizin yolundan (parent URL), "
    "tarih bilgilerinden ve metinden sezgisel çıkarım (intuition) yaparak değerlendir: "
    "Sadece EMİN OLDUĞUN, KESİNLİKLE müşterinin diğer bankaların kampanya veya ürünleriyle doğrudan ve hızlıca "
    "KIYASLAMA YAPABİLECEĞİ Türkçe bir banka ürün, aktif kampanya, kâr payı/oran, masraf/ücret veya hizmet içeriği mi "
    "('musteri_icerigi': 'gerekli')? Eğer URL'inde/başlığında/içeriğinde genel kültür/kitap/tarihçe (/assets_book/ gibi), "
    "genel blog/yaşam yazısı (/blog/yasam/ gibi), kurumsal duyuru, faaliyet raporu, eski/süresi geçmiş bülten, hukuki/KVKK metni, banka içi prosedür veya "
    "doğrudan kıyaslanabilir somut bir bankacılık ürünü/kampanyası içermeyen herhangi bir içerik seziliyorsa "
    "KESİNLİKLE 'gereksiz' yaz ('musteri_icerigi': 'gereksiz'). Sadece kesinlikle kıyaslanabilir "
    "somut bankacılık içeriğinden emin olduğunda 'gerekli' yaz."
    "\n\nBOŞ ÇIKTI KURALI: temizleme sonucunda geriye kıyaslanabilir HİÇBİR "
    "içerik kalmıyorsa (sayfa tamamen menü/altbilgi/şablon/hukuki metinse) "
    "'content' alanını boş bırak VE 'musteri_icerigi' alanına MUTLAKA "
    "'gereksiz' yaz. Boş içerik = gereksiz; bu iki alan asla çelişmemeli."
    "\n\nZORUNLU ALANLAR: JSON'da AŞAĞIDAKİ ALANLARIN HEPSİ HER ZAMAN bulunmalı. "
    "Bir bilgiden emin değilsen alanı ATLAMA — varsayılan değerini yaz: "
    "'content' bilinmiyorsa \"\", 'gecerlilik_baslangic'/'gecerlilik_bitis' "
    "bilinmiyorsa \"\", 'durum' bilinmiyorsa \"bilinmiyor\", "
    "'musteri_icerigi' emin değilsen \"gerekli\" (belirsizlikte veri kaybetme)."
)

# JSON parantezleri {{ }} ile kaçırıldı — çağıranın .format(...) çağrısı tek'e indirir.
JSON_HEAD = '\n\nSADECE JSON: {{"content": "<'
JSON_TAIL = ('>", "gecerlilik_baslangic": "<YYYY-MM-DD ya da boş>", '
             '"gecerlilik_bitis": "<YYYY-MM-DD ya da boş>", '
             '"durum": "<gecerli|suresi_gecmis|bilinmiyor>", '
             '"musteri_icerigi": "<gerekli|gereksiz>"}}')

# Talimat gövdesi — her iki aşama da bunu kullanır.
BASLIK = GOAL + DATE_RULE + RELEVANCE_RULE


# --- HIZLI ETİKET (ön-eleme) ----------------------------------------------
# Kullanıcı kararı 2026-08-23: "Önce hızla gerekli gereksiz analizi, sonra
# clean text isteyelim. Bu süreci çok hızlandırır."
#
# NEDEN AYRI PROMPT: ön-eleme eskiden TAM temizleme prompt'unu çağiriyordu,
# yani model 8196 karakterlik metni BASTAN YAZIYORDU sadece bir etiket almak
# icin. Olculdu 2026-08-23: tek istek ~135s, tunelin 120s limitini asiyordu.
# Bu prompt yalniz TEK KELIME dondurur -> yanit birkac token, saniyeler icinde.
#
# VERI KAYBI YOK: bu istek hicbir seyi diske yazmaz, yalniz 'gerekli' mi
# 'gereksiz' mi kararini verir. 'gerekli' cikan her metin sonra TAM temizleme
# surecinden gecer, hicbir karakteri kirpilmadan.
ETIKET_Q = (
    "Asagidaki KATILIM BANKASI web metnini SINIFLANDIR." + RELEVANCE_RULE +
    "\n\nMETNI TEMIZLEME, YENIDEN YAZMA, OZETLEME. Sadece siniflandir."
    "\n\nSADECE JSON: {{\"musteri_icerigi\": \"gerekli\"}} ya da "
    "{{\"musteri_icerigi\": \"gereksiz\"}}"
    "\n\nURL: {url}{title_line}\nMETIN:\n{body}"
)
