"""Sınıflandırma ajanları — iki ayrı adımda, iki ayrı güven seviyesiyle çalışır:

  classify_page   : bir SAYFANIN (henüz araştırma yapılmamış, tek başına) (1)
                    kıyaslanabilir SOMUT bir ürün/kampanya olup olmadığına, (2)
                    mevcut tablo havuzunda AYNI konuyu kıyaslayan bir tablo olup
                    olmadığına dair ERKEN/UCUZ bir tahmin verir — pahalı 10-banka
                    fan-out'u tetiklemeden önceki filtre (fan_out_one mu fan_out_all
                    mı sorusuna cevap verir, bkz. pipeline.py). Bu tahmin sadece TEK
                    bir sayfanın metnine dayanır, yanılabilir — AUTORİTE değil.

  finalize_table  : tablo TAMAMEN kurulduktan SONRA (synth.build_row'un sıralı
                    inşası bittikten sonra) TEK seferde çalışan SON gözden geçirme.
                    Elindeki tablonun TAM haline bakarak: (1) sütunları gerekirse
                    SIKILAŞTIRIR (compact — seyrek/örtüşen sütunları birleştirir,
                    veri kaybetmeden), (2) search_tables ile mevcut havuzda
                    GERÇEKTEN aynı olan başka bir tablo var mı SON kez kontrol eder
                    (varsa duplicate_of ile işaretler — pipeline bunu AYRI tablo
                    açmak yerine merge_tables ile birleştirir), (3) ana kategori +
                    alt-kategori (synth.closest_subcategories ile kelime+anlam
                    benzerliğine dayalı somut adaylar üzerinden) + tablo ADI + (4)
                    docstring'i TEK seferde, KALICI olarak verir. Bu, classify_page'in
                    erken/ucuz tahmininin AKSİNE — tüm bankaların GERÇEK, işlenmiş
                    verisini gördüğü için otoriter kabul edilir.

Tablo havuzu büyüdükçe (yüzlerce olabilir) TÜMÜNÜ tek prompt'a sığdırmak mümkün
değil — her iki ajan da bank_agent'taki search_bank ile AYNI felsefe: search_tables
(embedding bazlı arama) aracı veriyoruz, LLM karar verene kadar özgürce (farklı
sorgularla) tekrar arayabilir. Karar tamamen modele ait; burada içerik kararına
karışan bir kural yok, yalnızca mühendislik güvenlikleri (bank_agent'la aynı
desen: kaynak bütçesi + dayanıklı istek + sıcaklık merdiveni)."""
from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from llm import get_llm
from llm.providers.vllm_provider import reset_http_pool

from config import tunnel

from . import store
from ..net_limit import NET_SEM
from .json_mod import llm_kwargs
from .retrieval import make_table_search_tool
from .synth import _restore_lost_values, _subcat_block

log = logging.getLogger("dataprep.compare.classify_agent")

# Sayfa gövdesi bu boyu aşarsa (nadir — çoğu banka sayfası/PDF'i altında kalır,
# ama ör. 133 sayfalık bir izahname/rehber PDF'i çok aşabilir) TAM metin yerine
# İKİ AŞAMALI bir yol izlenir. DENENDİ, İŞE YARAMADI: sabit sayıda örnek (baş+son
# + eşit aralık) — dokümanı TAMAMEN kapsamıyor, ARADAKİ boşluklarda kalan tek
# bir sayfadaki ürün/kampanya sessizce KAÇIRILABİLİYOR (canlı test: 133 "sayfalık"
# bir dokümanın 61. sayfasına gömülü kampanya, 15 örnek arasındaki boşluğa denk
# gelip HİÇ görülmedi, comparable=false döndü — kanıtlı, ölçülmüş hata).
#
# Bunun yerine: doküman WIN'lik pencerelerle BOŞLUKSUZ döşenir (tam kapsama,
# hiçbir bölge atlanmaz), her pencere UCUZ+ARAÇSIZ bir "burada karşılaştırılabilir
# bir ürün/kampanya var mı?" taramasından geçer — TÜM pencereler PARALEL çalışır
# (bank_agent'taki fan-out ile aynı desen), bu yüzden pencere SAYISI arttıkça
# gecikme neredeyse ARTMAZ (tek dalga). Sadece
# "isabet" diyen pencereler asıl PAHALI adıma (search_tables aracılı tam
# classify çağrısı) gider — 133 sayfalık bir PDF'in maliyeti "1 dalga ucuz
# tarama + 1 gerçek classify" olur, 133 gerçek çağrı değil.
_SINGLE = 40000                  # bu boya kadar TAM metin, tarama gerekmez
# REPO GENELİNDEKİ TEK SINIR (kullanıcı kararı 2026-08-22): 8196 karakter,
# %10 (820) overlap — dataprep/embed.py::CHUNK/OVERLAP ve
# config/settings.py::INDEX_MAX_CHUNK_CHARS ile AYNI değerler. Bu bir
# KIRPMA değildir: belge 8196'yı aşarsa overlap'li pencerelere BÖLÜNÜR,
# hepsi taranır — hiçbir metin atılmaz.
_SCREEN_WIN, _SCREEN_OVERLAP = 8196, 820
# Bir "isabet" penceresi gerçek (pahalı) sınıflandırmaya gitmeden önce her iki
# yandan bu kadar EK bağlamla genişletilir — DENENDİ, İŞE YARAMADI: sabit bir
# pencere sayısı tavanı (_MAX_SCREEN_WINDOWS=60) koyup üstünü eşit aralıklı
# alt-örneklemeye düşürmüştük; 62 pencere üreten bir dokümanda tam da kritik
# içeriği taşıyan pencere bu indirgemede SESSİZCE DÜŞTÜ (canlı test: 133
# "sayfalık" dokümanın 100. sayfasındaki ikinci kampanya HİÇ görülmedi — kanıtlı
# veri kaybı). Bu yüzden artık pencere sayısına TAVAN YOK — tarama ucuz+paralel
# olduğu için buna gerek yok, "asla bilgi kaybı" ilkesi tavan koymaktan daha önemli.
# Genişletme İKİ katmanlı: (1) DETERMİNİSTİK güvenlik ağı — her isabet penceresi
# gerçek sınıflandırmaya gitmeden önce otomatik olarak _PAD kadar büyütülür,
# hiçbir LLM kararına bağlı değil, bedava (elimizde zaten TÜM doküman bellekte).
# (2) Modelin KENDİ kararı — read_more aracı (aşağıda) ile, gerçek sınıflandırma
# sırasında istediği kadar öncesine/sonrasına ilerleyebilir; TF26'nın genel
# ilkesiyle uyumlu: sınırı BİZ çizmek yerine karar modele ait olsun. İkisi
# birlikte: (1) hiçbir ek çağrı gerektirmeden temel bir taban sağlıyor, (2) o
# taban yetmezse modelin kendi inisiyatifiyle sınırsız ilerlemesine izin veriyor.
_PAD = 2000
# 30 -> 25 (kullanıcı kararı 2026-08-23): hiçbir aşamada vLLM'e 25'ten fazla
# eşzamanlı istek gitmemeli. NET_SEM zaten kısardı ama bu havuz 30 açıp
# kuyruk şişiriyordu; kaynağında sınırlandı.
_MAX_PARALLEL_SCREENS = 25       # TEK bir sayfanın taramasında yerel eşzamanlılık
                                  # tavanı (gecikmeyi kontrol eder, kapsamı KISMAZ
                                  # — hâlâ TÜM pencereler taranır, sadece dalga dalga)

_SCREEN_Q = (
    "Bu, daha büyük bir KATILIM BANKASI sayfası/PDF'inin BİR PARÇASI (kesik "
    "başlayıp/bitebilir, önemli değil). SADECE bu parçaya bakarak: burada RAKİP "
    "bankalarla karşılaştırılabilir SOMUT bir ÜRÜN ya da KAMPANYA anlatılıyor "
    "mu (genel bir hizmet/menü/arka plan bilgisi/hukuki metin DEĞİL)? Emin "
    "değilsen 'evet' de (bu SADECE ucuz bir ön-tarama, kesin karar değil — "
    "kaçırmaktansa fazladan incelemek daha güvenli).\n\n"
    "Parça:\n\"\"\"{chunk}\"\"\"\n\n"
    'SADECE JSON: {{"hit": true|false}}')


def _window_starts(body_len: int) -> list[int]:
    """Dokümanı WIN'lik, BOŞLUKSUZ (overlap ile) pencerelere döşeyen başlangıç
    konumları — TAM kapsama, HİÇBİR pencere atlanmaz/indirgenmez."""
    return list(range(0, max(body_len - _SCREEN_OVERLAP, 1), _SCREEN_WIN - _SCREEN_OVERLAP))


def _screen_window(chunk: str) -> bool:
    """Ucuz, araçsız ön-tarama: bu parça karşılaştırılabilir bir ürün/kampanya
    gibi mi görünüyor? Hata olursa (LLM ulaşılamadı) MUHAFAZAKÂR davranır —
    pencereyi ATMAK yerine DAHİL eder (fits_table'daki 'LLM ulaşılamadı ->
    tabloya güven' ilkesiyle aynı: belirsizlikte veri kaybına yol açma)."""
    try:
        # Araçsız ön-tarama, düz JSON bekler -> vLLM JSON zorlaması açık.
        llm = get_llm("gemma", temperature=0.0, **llm_kwargs(True))
        _t0 = time.time()
        with NET_SEM:
            ai = llm.invoke([SystemMessage(_SCREEN_Q.format(chunk=chunk))])
        # Uyarlanabilir sınırlayıcı geri bildirimi (bkz. net_limit.py). Bu çağrı
        # KASITLI olarak çok küçük bir tarama sorusu — hızlı bitmesi normaldir ve
        # sistemin rahat olduğunun iyi bir göstergesidir.
        NET_SEM.report(ok=True, duration=time.time() - _t0)
        d, _hata = _try_parse((ai.content or "").strip())
        return bool(d is None or d.get("hit", True))
    except Exception:                          # noqa: BLE001
        NET_SEM.report(ok=False)               # tıkanıklık sinyali
        return True


def _screened_chunks(body: str) -> list[str]:
    """Uzun bir dokümanı BOŞLUKSUZ pencerelere böler, hepsini PARALEL tarar,
    "isabet" diyenleri döner — GENİŞLETİLMİŞ (_PAD kadar her iki yandan ek
    bağlamla) olarak.

    Neden genişletme: önemli bir bilgi tam pencere sınırına denk gelip ikiye
    bölünebilir (senin sorduğun senaryo). Model'e "daha fazla bağlam iste"
    diye bir araç (scroll/genişlet) VERMEK yerine bunu DETERMİNİSTİK olarak
    biz yapıyoruz — çünkü model'in TAM OLARAK neyi kaçırdığını fark edip
    araç çağırması güvenilir değil (ortadan kesik bir cümle her zaman "eksik"
    gibi görünmeyebilir, tutarlı ama yanlış bir bütünlük izlenimi verebilir);
    zaten elimizde TÜM doküman bellekte olduğu için genişletmek EK bir LLM
    çağrısı gerektirmiyor, bedavaya güvenlik sağlıyor. Örtüşen pencereler bu
    yüzden aynı içeriği birden çok kez sınıflandırabilir (iki komşu pencere
    de isabet dönerse) — bu bilinçli bir tercih, mükerrer table eşleşmesi
    zaten finalize_table/dedup.py'de ELE ALINIYOR; bilgiyi KAÇIRMAK bunun
    aksine GERİ DÖNÜLEMEZ.

    Hiçbir pencere isaret vermezse (nadir, taramanın kendisi de yanılabilir)
    yine de boş dönmemek için baş+son GÜVENLİK AĞI olarak eklenir.

    Döndürülen (start, end) sınırları, gerçek sınıflandırma sırasında
    read_more aracının "şu an gördüğün metnin dokümandaki gerçek konumu
    burası" diye bilmesi için — bu olmadan araç nereden devam edeceğini
    bilemez."""
    starts = _window_starts(len(body))
    windows = [body[s:s + _SCREEN_WIN] for s in starts]
    with ThreadPoolExecutor(max_workers=min(len(windows), _MAX_PARALLEL_SCREENS)) as ex:
        hits = list(ex.map(_screen_window, windows))
    picked_starts = [s for s, h in zip(starts, hits) if h]
    if not picked_starts:
        picked_starts = [starts[0], starts[-1]]
    out = []
    for s in picked_starts:
        lo, hi = max(0, s - _PAD), min(len(body), s + _SCREEN_WIN + _PAD)
        out.append((lo, hi, body[lo:hi]))
    return out


class _ReadMoreArgs(BaseModel):
    direction: str = Field(description="'öncesi' (şu an gördüğün metnin HEMEN "
                            "öncesini oku) ya da 'sonrası' (HEMEN sonrasını oku).")


def _make_read_more_tool(body: str, bounds: dict) -> StructuredTool:
    """Şu an gördüğün metin dokümanın bir KESİTİdir — bu araçla öncesine/
    sonrasına doğru istediğin kadar ilerleyebilirsin, SABİT bir sınır yok
    (dokümanın gerçek başına/sonuna kadar). `bounds` closure'da tutulur ve
    her çağrıda genişler, böylece art arda çağrılar dokümanda git gide daha
    ileri/geri gider (aynı yeri tekrar tekrar getirmez)."""
    STEP = 4000

    def _run(direction: str) -> str:
        if direction == "öncesi":
            new_start = max(0, bounds["start"] - STEP)
            if new_start == bounds["start"]:
                return "Dokümanın başına zaten ulaştın, öncesinde daha fazla metin yok."
            text = body[new_start:bounds["start"]]
            bounds["start"] = new_start
        else:
            new_end = min(len(body), bounds["end"] + STEP)
            if new_end == bounds["end"]:
                return "Dokümanın sonuna zaten ulaştın, sonrasında daha fazla metin yok."
            text = body[bounds["end"]:new_end]
            bounds["end"] = new_end
        return text

    return StructuredTool.from_function(
        func=_run, name="read_more", args_schema=_ReadMoreArgs,
        description=("Şu an gördüğün metin dokümanın bir KESİTİdir (kesik başlayıp/"
                     "bitebilir). Bir cümle/bilgi yarım görünüyorsa ya da öncesinde/"
                     "sonrasında önemli bağlam olabileceğini düşünüyorsan bu araçla "
                     "dokümanın HEMEN öncesini ya da sonrasını okuyabilirsin — "
                     "istediğin kadar tekrar çağırıp ilerleyebilirsin, sabit bir "
                     "sınır yok (dokümanın gerçek başına/sonuna kadar)."))


# 10 -> 100 (kullanıcı kararı 2026-08-22): sonsuz döngü emniyeti, VERİ
# sınırı DEĞİL — bir konu yanlışlıkla 'bulunamadı' sayılmasın.
MAX_CALLS = 100                # bir sınıflandırma için arama bütçesi
# Aynı sorguyu next=true ile kaç kez derinleştirebilir (sayfalama). search_bank'taki
# 42-derinlik olayı gibi sınırsız bir sızıntıya karşı fiziksel tavan — bunun
# ötesi azalan getiri sayılır; genel tekrar-kontrolü next zincirini yakalayamıyor
# çünkü her adımda offset değişiyor, teknik olarak "farklı" bir çağrı oluyor.
NEXT_DEPTH_LIMIT = 50
_TEMP_LADDER = (0.0, 0.3, 0.6, 1.0)

_SYSTEM = (
    "Sen bir KATILIM BANKASI sayfa-sınıflandırma ajanısın. BUGÜNÜN TARİHİ: {today} "
    "— geçerlilik/süre ile ilgili değerlendirmelerini buna göre yap. Sana bir sayfanın "
    "metni verilecek. ÖNEMLİ — TERMİNOLOJİ: katılım bankacılığı faizsiz/İslami "
    "bankacılıktır; ÜRETTİĞİN HER METİNDE (topic dahil) 'kredi'/'faiz' değil "
    "'finansman'/'kâr payı'/'kâr oranı' kullan. Kaynak sayfa konvansiyonel terim "
    "kullansa bile SEN katılım bankacılığı terimine çevir. Tek istisna: 'kredi "
    "kartı' yerleşik bir ÜRÜN ADI olduğu için olduğu gibi kalır.\n\n"
    "search_tables aracı embedding (anlam vektörü) tabanlı arama yapar — sorgun "
    "ile aranan arasındaki vektör karşılaştırması için modelin bir 'niyet' "
    "bilgisine ihtiyacı var; her çağrıda intent alanına o aramayla TAM OLARAK ne "
    "bulmaya çalıştığını yaz — bu formalite değil, arama kalitesini gerçekten "
    "belirliyor. Her çağrı 5 aday getirir; yetmezse AYNI sorguyla next=true "
    "gönderip SONRAKİ 5'i görebilirsin, bunu yaparken önceki adaylardan işine "
    "yaramayanları not_useful ile işaretle (geçmişten silinir), işine yarayanları "
    "useful ile işaretle — böylece sadece gerçekten kullanışlı adaylar elde "
    "kalır.\n\n"
    "Sana verilen sayfa metni daha büyük bir dokümanın bir KESİTİ olabilir (kesik "
    "başlayıp/bitebilir). Bir cümle/bilgi yarım görünüyorsa ya da öncesinde/"
    "sonrasında önemli bağlam olabileceğini düşünüyorsan read_more(direction) "
    "aracıyla dokümanın hemen öncesini/sonrasını okuyabilirsin — istediğin kadar "
    "tekrar çağırıp ilerleyebilirsin, sabit bir sınır yok.\n\nGörevin:\n\n"
    "1) Sorulacak soru tam olarak şu: bu sayfa, RAKİP bankalarla KARŞILAŞTIRILABİLİR "
    "bir KAMPANYA mı (bir promosyon/kazanım teklifi), YA DA RAKİP bankalarla "
    "KARŞILAŞTIRILABİLİR bir ÜRÜN mü (müşterinin bağımsız olarak edinebileceği "
    "somut bir finansal ürün: finansman, hesap, kart, yatırım aracı vb.)? Değilse "
    "— bankanın genel bir HİZMETİ/işlevi (dijital kanal özelliği, altyapı/iş "
    "birliği bilgisi, güvenlik, başvuru süreci gibi operasyonel bir yetenek) ya da "
    "daha derin/arka plan bir bilgiyse — comparable=false de; sayının/oranın somut "
    "olması tek başına yeterli değil, asıl soru bunun bağımsız edinilebilen bir "
    "ürün ya da katılınabilen bir kampanya olup olmadığı. Sayfa birçok farklı "
    "ürünü listeleyen bir MENÜ/genel-bakış sayfasıysa (kategorinin tamamını "
    "anlatıyorsa, tek bir somut ürünü değil) de comparable=false de — "
    "o ürünlerin her biri kendi sayfasında ayrıca karşına çıkacak.\n\n"
    "2) Kıyaslanabilirse: search_tables aracıyla mevcut karşılaştırma tablosu "
    "havuzunda bu ürün/kampanyayla AYNI ŞEY olan bir tablo var mı ara. Kararını "
    "açıklamaların ANLAMINA göre ver, kelime benzerliğine değil — gördüğün TAM "
    "sayfa metniyle kıyasla. YENİ TABLO AÇMAK PAHALI bir işlemdir (10 bankaya tam "
    "araştırma tetikler) — bu yüzden 'yeni konu' kararına varmadan önce en az "
    "birkaç FARKLI ifadeyle gerçekten aradığından emin ol, tek aramaya güvenip "
    "vazgeçme.\n\n"
    "   KARAR ÖLÇÜTÜ — şu soruyu sor: bu sayfadaki bilgi, o tablonun MEVCUT "
    "SATIRLARINDAN BİRİNE ya da YENİ BİR SÜTUNUNA sığar mı? Sığıyorsa EŞLEŞTİR. "
    "Ayrı tablo ancak kıyaslanacak BOYUTLAR (sütunlar) temelden farklıysa "
    "gerekir. Somutlaştırmak için kendine şunu sor: 'bu iki şeyi yan yana aynı "
    "tabloda görmek bir müşteriye MANTIKLI gelir mi?' — evetse tek tablo.\n"
    "   Aynı ürün ailesinin farklı adları, farklı hedef kitlesi (bireysel/ticari), "
    "farklı vadesi/tutarı ya da farklı bir bankanın kendi markası TEK BAŞINA ayrı "
    "tablo sebebi DEĞİLDİR — bunlar o tablonun bir SÜTUNU ya da bir SATIRI olur.\n"
    "   BUNUN TERSİ DE GEÇERLİ: gerçekten farklı bir ihtiyacı karşılayan, farklı "
    "boyutlarla kıyaslanan bir ürünü zorla mevcut bir tabloya sıkıştırma. "
    "Örneğin bir sigorta ürünüyle bir finansman ürünü aynı tabloda kıyaslanamaz; "
    "sütunları örtüşmez. Emin olamadığın, gerçekten sınırda bir durumda AYRI "
    "tablo aç — yanlış birleştirme veri kaybettirir, ayrı kalan tablo ise sonda "
    "dedup adımıyla birleştirilebilir.\n"
    "   Yani: kolay eşleşmeleri KAÇIRMA (bu asıl sorundur), ama alakasız olanı da "
    "zorla uydurma. İlk elden isabetli olman süreci hızlandırır.\n\n"
    "3) HER durumda (eşleşse de eşleşmese de) bu sayfanın ASIL ANLATTIĞI somut "
    "ürün/kampanya TÜRÜNÜ tanımlayan bir konu adı belirle. Bunu bir insan analist "
    "gibi düşünerek karar ver: bu gerçekten TEK BAŞINA ayrı, anlamlı bir ürün/"
    "kampanya mı — yoksa (a) birçok farklı ürünü birden kapsayan bir ÜST BAŞLIK mı, "
    "(b) bir ürünün yalnızca dar bir alt-koşulu/detayı mı, ya da (c) zaten var olan "
    "bir ürün/kampanyayla AYNI temel AMACA/HEDEFE hizmet eden, sadece somut "
    "mekanizması (nasıl uygulandığı, hangi araçla sağlandığı, hangi koşullarla "
    "sunulduğu) farklılaşan bir VARYANTI mı? Bu ayrım hem ÜRÜNLER hem KAMPANYALAR "
    "için aynı şekilde geçerlidir — hedef/amaç aynıysa, sadece uygulama şekli "
    "farklıysa bu tek bir ailedir. (a), (b) ve (c) durumlarında bunu ayrı bir "
    "konu SAYMA — (c) durumunda search_tables ile o temel ürün/kampanya ailesinin "
    "tablosunu ara ve varsa ona eşleştir; farklılaşan detay o tablonun bir "
    "SÜTUNU olabilir, ayrı tablo açma sebebi değildir. "
    "Konu adı bankanın kendi verdiği MARKA/ÜRÜN ADINI içermesin — marka adları "
    "banka-özeldir, başka bankada aranmaz; bunun yerine ürünün/kampanyanın "
    "sektörde genel kabul gören TÜRÜNÜ/HEDEFİNİ tanımla. Bunu yaparken SADECE "
    "markayı/ticari ismi çıkar — sayfanın anlattığı somut hedef kitleyi, "
    "sektörü ya da mekanizmayı KAYBETME; konu ne kadar dar/niş olursa olsun "
    "kapsamını gereğinden fazla genişletip başka bir ürün/hizmet ailesine "
    "dönüştürme (aşırı genelleme, sonraki aramalarda bu sayfanın bir daha "
    "bulunamamasına yol açar). Eşleşme bulduysan bu "
    "konu adı mevcut tablonun konusuyla TUTARLI olsun (sonraki araştırmalarda "
    "kullanılacak).\n\n"
    "Emin olunca SADECE şu JSON ile (tool çağırmadan) bitir:\n"
    '{{"comparable": true|false, "fits_table": "<eşleşen mevcut tablo id\'si ya '
    'da boş>", "topic": "<bu sayfanın anlattığı konu adı, banka adı geçmesin>", '
    '"topic_aciklama": "<bu konunun NE OLDUĞUNU 1-2 cümleyle, DİĞER BANKALARDA '
    'ARANABİLECEK şekilde tarif et: ürünün/kampanyanın temel mekanizması, kime '
    'sunulduğu, hangi ihtiyacı karşıladığı. Marka adı KULLANMA. Bu açıklama 10 '
    'ayrı bankaya araştırmacı olarak gönderilecek — onlar sayfayı GÖRMEYECEK, '
    'sadece bu tarifi okuyacak, o yüzden tek başına yeterli olsun.>"}}\n\n'
    "Sayfa URL: {url}\n\nSayfa metni (çok uzun bir belgenin BİR PARÇASI olabilir "
    "— kesik başlayıp/bitebilir, önemli değil; SADECE bu parçaya bak, kendi "
    "başına değerlendir):\n\"\"\"{body}\"\"\""
)


def _is_permanent(exc: Exception) -> bool:
    """GERÇEKTEN kalıcı 4xx mi? 400/403 BİLEREK listede DEĞİL — tünel soketi
    bayatlayınca nginx "400 / 0 byte" döndürüyor ve istek sunucuya HİÇ
    ulaşmıyor; aynı istek saniyeler sonra 200 dönüyor (sunucu access log'uyla
    kanıtlandı, 2026-08-18). 403 de tünelden geliyor. Bunları kalıcı sayıp pes
    etmek işi sessizce yarıda bırakırdı. Bkz. vlm.py::_GECICI_4XX."""
    s = str(exc)
    return any(code in s for code in ("401", "404", "413", "422", "BadRequest"))


def _invoke_resilient(tools, messages, allow_tools: bool = True, start_attempt: int = 0):
    """ASLA PES ETMEZ (60s'de sabitlenen üstel backoff) — kalıcı (4xx) hatada
    denemez, hemen fırlatır. Uzun süredir başarısızsa [CLASSIFY_UZUN_SÜRELİ_HATA]
    ile uyarır ama DURMAZ."""
    start = time.time()
    delay = 1.0
    attempt = start_attempt
    last_warn = 0.0
    while True:
        t = _TEMP_LADDER[min(attempt, len(_TEMP_LADDER) - 1)]
        try:
            # Araç bağlanmayacaksa düz JSON bekleniyor -> vLLM JSON zorlaması
            # (bkz. json_mod.py). Araçla birlikte kullanılmaz.
            _arac_var = bool(allow_tools and tools)
            llm = get_llm("gemma", temperature=t, **llm_kwargs(not _arac_var))
            if _arac_var:
                llm = llm.bind_tools(tools)
            _t0 = time.time()
            with NET_SEM:
                _res = llm.invoke(messages)
            # UYARLANABILIR SINIRLAYICI geri bildirimi (bkz. net_limit.py):
            # hizli biten istek -> eszamanlilik artirilabilir; tunelin omur
            # sinirina yaklasan istek -> artirilmamali.
            NET_SEM.report(ok=True, duration=time.time() - _t0)
            return _res
        except Exception as exc:
            if _is_permanent(exc):
                raise
            elapsed = time.time() - start
            # BAĞLANTI GÜVENLİĞİ (kullanıcı kararı 2026-08-22, önceki
            # aşamalarla AYNI): hata aldığımız bağlantıya bir daha
            # dönülmez — havuz TAMAMEN kapatılır, tazesi açılır.
            reset_http_pool(f"{type(exc).__name__}")
            tunnel.refresh_if_needed()      # büyük ihtimalle tünel URL'i değişti
            if elapsed - last_warn >= 300:
                log.warning("    [CLASSIFY_UZUN_SÜRELİ_HATA] %.0fs'dir başarısız (deneme %d): %s — "
                            "denemeye DEVAM ediyor (durmuyor)", elapsed, attempt + 1, type(exc).__name__)
                last_warn = elapsed
            else:
                log.warning("    classify istek hatası (deneme %d, %.0fs): %s",
                            attempt + 1, elapsed, type(exc).__name__)
            time.sleep(delay)
            delay = min(delay * 2, 60)
            attempt += 1


def _try_parse(text: str, bekleyen: tuple[str, ...] = ()) -> tuple[dict | None, str]:
    """(sonuç, hata_metni). Hata metni modele AYNEN geri verilir — genel bir
    "geçersizdi" uyarısı yerine SOMUT sorun (parse hatası ya da eksik alan)
    bildirilirse model düzeltmeyi hedefli yapar."""
    if not text:
        return None, "Cevap boş geldi."
    d = None
    hata = ""
    try:
        d = json.loads(text)
    except json.JSONDecodeError as exc:
        hata = f"JSON ayrıştırılamadı: {exc}"
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                d = json.loads(m.group(0))
                hata = ""
            except json.JSONDecodeError as exc2:
                hata = f"JSON ayrıştırılamadı: {exc2}"
    if d is None:
        return None, hata or "Cevapta JSON bulunamadı."
    if not isinstance(d, dict):
        return None, f"Beklenen bir JSON nesnesi, gelen: {type(d).__name__}."
    eksik = [k for k in bekleyen if k not in d]
    if eksik:
        return None, (f"JSON'da şu alanlar eksik: {', '.join(eksik)}. "
                      f"Gelen alanlar: {', '.join(sorted(d)) or '(hiç)'}.")
    return d, ""


def _parse_json_ladder(messages: list, first_content: str, _cycles: int = 2,
                        bekleyen: tuple[str, ...] = ()) -> dict | None:
    """JSON bozuksa ya da beklenen alanlar eksikse yeniden cevap ister.

    Sıra (kullanıcı kararı 2026-08-20) — her sıcaklıkta ÖNCE feedback'siz,
    SONRA somut hatayı içeren feedback'li deneme:
        0.0 normal -> 0.0 feedback -> 0.3 normal -> 0.3 feedback -> ...
    Feedback bulunan SOMUT hatayı prompt'un EN SONUNA ekler ve "bu hatayı
    vermeden dene" der. Merdiven tükenirse 0.0'a resetlenip _cycles tur
    tekrarlanır."""
    d, hata = _try_parse((first_content or "").strip(), bekleyen)
    if d is not None:
        return d
    for cycle in range(_cycles):
        for i in range(len(_TEMP_LADDER)):
            for feedbackli in (False, True):
                if cycle == 0 and i == 0 and not feedbackli:
                    continue            # first_content'te zaten denendi
                msgs = messages
                if feedbackli:
                    msgs = messages + [HumanMessage(
                        "Önceki cevabında şu hata vardı: " + hata +
                        "\nBu hatayı vermeden SADECE geçerli JSON döndür, "
                        "başka hiçbir şey yazma.")]
                try:
                    ai = _invoke_resilient(None, msgs, allow_tools=False,
                                           start_attempt=i)
                except Exception as exc:
                    log.warning("    JSON merdiveni istek hatası: %s", exc)
                    continue
                d, hata = _try_parse((ai.content or "").strip(), bekleyen)
                if d is not None:
                    return d
        if cycle + 1 < _cycles:
            log.info("    (sıcaklık merdiveni tükendi — 0.0'a resetlenip tekrar)")
    log.warning("    JSON hiçbir sıcaklıkta düzelmedi (%s)", hata)
    return None

def _classify_chunk(chunk: str, url: str, body: str, start: int, end: int) -> dict | None:
    """`chunk` gösterilen metin, `body`/`start`/`end` bunun dokümandaki gerçek
    konumu (read_more aracının öncesine/sonrasına ilerleyebilmesi için)."""
    marked: set[str] = set()
    discarded: set[str] = set()
    search_tool = make_table_search_tool(store.load_registry, marked, discarded)
    read_more_tool = _make_read_more_tool(body, {"start": start, "end": end})
    tools = [search_tool, read_more_tool]
    by_name = {t.name: t for t in tools}
    system = _SYSTEM.format(url=url or "-", body=chunk, today=date.today().isoformat())
    messages = [SystemMessage(system), HumanMessage("Sınıflandır.")]
    calls = 0
    repeat_counts: dict = {}   # search_tables: sorgu -> tekrar say; diğer araçlar: (ad,argüman) imzası -> tekrar say
    next_depth: dict[str, int] = {}      # aynı sorgu next=true ile kaç kez derinleştirildi
    stuck = False                        # bank_agent'taki İLE AYNI kanıtlı desen
    ai: AIMessage
    while True:
        ai = _invoke_resilient(tools, messages)
        messages.append(ai)
        if not ai.tool_calls:
            break
        if calls >= MAX_CALLS or stuck:
            reason = ("Aynı çağrıyı tekrarlıyorsun, yeni bilgi gelmiyor."
                      if stuck else "Arama bütçen doldu.")
            messages.append(HumanMessage(
                f"{reason} Daha fazla araç çağırma; şimdiye kadar gördüklerinle "
                "SADECE JSON formatında karar ver."))
            ai = _invoke_resilient(None, messages, allow_tools=False)
            messages.append(ai)
            break
        for tc in ai.tool_calls:
            calls += 1
            name = tc.get("name", "search_tables")
            args = tc.get("args") or {}
            if name not in by_name:
                out = f"'{name}' diye bir araç yok."
                messages.append(ToolMessage(out, tool_call_id=tc["id"]))
                continue
            tool = by_name[name]
            if name == "search_tables":
                q = args.get("query", "").strip().lower()
                if args.get("next"):
                    n = next_depth.get(q, 0) + 1
                    next_depth[q] = n
                    if n > NEXT_DEPTH_LIMIT:
                        stuck = True
                else:
                    n = repeat_counts.get(q, 0) + 1
                    repeat_counts[q] = n
                    if n >= 3:
                        stuck = True
            else:
                # read_more DAHİL search_tables dışındaki araçlar — bank_agent'taki
                # AYNI genel (araç, argüman) imza tavanı: harfiyen aynı çağrı 3. kez
                # -> TIKANMIŞ. read_more zaten dokümanın gerçek ucuna varınca kendi
                # kendine "daha fazla yok" diyor, bu sadece EK bir fiziksel güvence.
                sig = (name, tuple(sorted((k, str(v)) for k, v in args.items())))
                n = repeat_counts.get(sig, 0) + 1
                repeat_counts[sig] = n
                if n >= 3:
                    stuck = True
            try:
                out = tool.invoke(tc["args"])
            except Exception as exc:
                out = f"HATA: {exc}"
            messages.append(ToolMessage(str(out), tool_call_id=tc["id"]))

    d = _parse_json_ladder(messages, ai.content,
                            bekleyen=("comparable", "topic"))
    if d is None:
        return None
    registry = store.load_registry()
    fits = (d.get("fits_table") or "").strip()
    if fits and not any(r["id"] == fits for r in registry):
        fits = ""                        # uydurma/geçersiz id -> yok say
    return {"comparable": bool(d.get("comparable")), "fits_table": fits,
            "topic": (d.get("topic") or "").strip(),
            # KONU AÇIKLAMASI: 10 araştırmacı ajan tetikleyici sayfayı GÖRMÜYOR,
            # yalnız konu ADINI alıyordu ("Otomatik Altın Biriktirme Hesabı").
            # Kısa bir isim mekanizmayı taşımaz; her araştırmacı farklı yorumlayıp
            # farklı şey arayabilir. Bu alan konunun NE olduğunu tarif eder ve
            # araştırmacı prompt'una eklenir (kullanıcı kararı 2026-08-25).
            "topic_aciklama": (d.get("topic_aciklama") or "").strip()}


def _safe_classify_chunk(chunk: str, url: str, body: str, start: int, end: int) -> dict | None:
    """ASLA istisna fırlatmaz — hata olursa None döner (çağıran bu parçayı atlar,
    diğer parçaların sonucunu SİLMEZ)."""
    try:
        return _classify_chunk(chunk, url, body, start, end)
    except Exception as exc:
        log.error("  SINIFLANDIRMA HATASI: %s: %s: %s", url, type(exc).__name__, exc)
        return None


def classify_page(body: str, url: str) -> list[dict] | None:
    """Bir sayfayı/PDF'i sınıflandırır. Kısa sayfa -> TEK karar (0 ya da 1
    elemanlı liste). Uzun sayfa -> ÖNCE ucuz+paralel bir tarama TÜM dokümanı
    boşluksuz kapsar (_screened_chunks — bilgi kaybı YOK, date_pass.py'nin
    windowing'iyle aynı felsefe), "isabet" diyen HER pencere kendi başına,
    SIRAYLA (bir öncekinden bağımsız, ayrı bir search_tables oturumuyla) gerçek
    sınıflandırmadan geçer. Bunun nedeni: uzun bir belge (ör. bir kampanya
    arşivi PDF'i) TEK bir ürünü değil BİRDEN FAZLA farklı ürünü/kampanyayı
    anlatıyor olabilir — hepsini TEK bir karara sıkıştırmak (eski tasarım)
    diğerlerini sessizce kaybediyordu.

    ASLA istisna fırlatmaz. Bir/birkaç parça işlenemezse (LLM ulaşılamadı)
    SADECE onlar atlanır, başarılı olan diğer parçaların sonucu KORUNUR —
    HİÇBİRİ işlenemezse None döner (üst katman retry sonra dener, own_verdict
    kaydedilmez)."""
    chunks = [(0, len(body), body)] if len(body) <= _SINGLE else _screened_chunks(body)
    if not chunks:
        return []                          # tarandı, hiçbir yerde aday yok -> kesin "hayır"
    results: list[dict] = []
    any_failed = False
    for start, end, chunk in chunks:
        d = _safe_classify_chunk(chunk, url, body, start, end)
        if d is None:
            any_failed = True
            continue
        results.append(d)
    if not results and any_failed:
        return None                        # hiçbir parça işlenemedi -> retry sonra
    return results


# --- tablo finalize (tablo TAMAMEN kurulduktan SONRA, tek seferlik SON karar) ---

_FINALIZE_SYSTEM = (
    "Sen bir KATILIM BANKASI karşılaştırma tablosu SON GÖZDEN GEÇİRME ajanısın. "
    "BUGÜNÜN TARİHİ: {today} — geçerlilik/süre ile ilgili değerlendirmelerini "
    "buna göre yap. "
    "'{topic}' konusunda bankaların araştırması bitip TAMAMLANMIŞ bir tablo "
    "elinde — artık ham raporlar değil, işlenmiş son hal. ÖNEMLİ — TERMİNOLOJİ: "
    "katılım bankacılığı faizsiz/İslami bankacılıktır; ÜRETTİĞİN HER METİNDE "
    "'kredi'/'faiz' değil 'finansman'/'kâr payı'/'kâr oranı' kullan. Tek "
    "istisna: 'kredi kartı' yerleşik bir ÜRÜN ADI olduğu için olduğu gibi kalır.\n\n"
    "search_tables aracı embedding (anlam vektörü) tabanlı arama yapar — her "
    "çağrıda intent alanına TAM OLARAK ne bulmaya çalıştığını yaz, arama "
    "kalitesini gerçekten belirliyor. Her çağrı 5 aday getirir; yetmezse AYNI "
    "sorguyla next=true gönderip SONRAKİ 5'i görebilirsin, işine yaramayanları "
    "not_useful ile eleyip işine yarayanları useful ile işaretleyerek.\n\n"
    "Görevin dört parça:\n\n"
    "1) SÜTUNLARI GEREKİRSE SIKILAŞTIR (compact): tablo, her banka kendi "
    "raporundaki özgün detayı ayrı bir sütun olarak eklediği için çok sayıda "
    "SEYREK (çoğu bankada boş) ve/veya anlamca ÖRTÜŞEN sütun biriktirmiş "
    "olabilir. Anlamca birbirine yakın sütunları TEK bir sütunda birleştir. "
    "SADECE gerçekten dağınık görünüyorsa birleştir — HİÇBİR GERÇEK BİLGİYİ "
    "KAYBETME, birleştirdiğin değerlerin İKİSİNİ DE koru (ör. '<A>; <B>'). "
    "Tablo zaten sıkı/mantıklıysa dokunma — 'compact' zorunlu bir hedef "
    "değil, sadece gerçekten saçma/dağınıksa.\n\n"
    "2) search_tables aracıyla mevcut tablo havuzunda BUNUNLA GERÇEKTEN AYNI "
    "ürün/kampanya TÜRÜNÜ kıyaslayan başka bir tablo var mı ara — bu SON "
    "kontrol, bu tablo henüz kaydedilmedi. Kararını açıklamaların ANLAMINA "
    "göre ver, kelime benzerliğine değil. Gerçekten aynıysa 'duplicate_of' "
    "alanına o tablonun id'sini yaz (bu durumda bu tablo AYRI kaydedilmeyecek, "
    "mevcut tabloyla birleştirilecek) — ama SADECE gerçekten eminsen; iki "
    "tablo yüzeysel benzese bile FARKLI bir hedefe/amaca hizmet ediyorsa "
    "'duplicate_of' YAPMA, ikisinin GERÇEKTEN aynı olduğunu garanti et. Emin "
    "değilsen boş bırak — yanlışlıkla birleştirmek, yanlışlıkla ayrı "
    "bırakmaktan daha kötü (ayrı bırakılan zaten dedup.py bakım ajanınca "
    "sonra taranır).\n\n"
    "3) Tabloya bir ANA KATEGORİ ata: 'kampanya' (bir promosyon/kazanım "
    "teklifi) ya da 'ürün' (bağımsız edinilebilen bir finansal ürün). Bir ALT "
    "KATEGORİ ata (UI'da filtrelemek için) — aşağıda mevcut alt kategoriler "
    "arasından kelime+anlam benzerliğine göre en YAKIN olanları, somut "
    "skorlarıyla sıraladık; bunlardan biri GERÇEKTEN aynı şeyi ifade ediyorsa "
    "MUTLAKA aynı ismi kullan (tutarlılık için), hiçbiri uymuyorsa yeni kısa "
    "bir ad üret:\n{subcats}\n\n"
    "4) Tablo ADINI (konu) netleştir — {topic!r} hâlâ isabetliyse aynen koru, "
    "tablonun son haline göre daha isabetli bir ad gerekiyorsa değiştir "
    "(banka/marka adı geçmesin, sektörde genel kabul gören TÜR/HEDEF). "
    "Docstring'i yaz: bu tablo neyi kıyaslıyor (1-2 cümle) — embedding'e "
    "çıkarılıp anlam bazlı aramada kullanılacak, ayırt edici olan NE "
    "cümlenin başında belirgin olsun, kalıplaşmış ortak bir çerçeve cümleye "
    "boğma; aynı zamanda açıklayıcı kal.\n\n"
    "Tablo:\n\"\"\"{table}\"\"\"\n\n"
    'Emin olunca SADECE şu JSON ile (tool çağırmadan) bitir:\n'
    '{{"duplicate_of": "<id ya da boş>", "topic": "<tablo adı>", '
    '"category": "kampanya"|"ürün", "subcategory": "<kısa alt kategori>", '
    '"docstring": "<1-2 cümle>", "columns": ["<sütun>", ...], '
    '"rows": {{"<banka>": {{"<sütun>": "<değer>", ...}}}}}}'
)


def _finalize_table(topic: str, table_data: dict, subcats: list[str] | None) -> dict | None:
    marked: set[str] = set()
    discarded: set[str] = set()
    search_tool = make_table_search_tool(store.load_registry, marked, discarded)
    tools = [search_tool]
    table_payload = json.dumps({"docstring": table_data.get("docstring", ""),
                                  "columns": table_data["columns"], "rows": table_data["rows"]},
                                 ensure_ascii=False)
    subcat_block = _subcat_block(topic, subcats)
    system = _FINALIZE_SYSTEM.format(topic=topic, subcats=subcat_block, table=table_payload,
                                     today=date.today().isoformat())
    messages = [SystemMessage(system), HumanMessage("Gözden geçir.")]
    calls = 0
    repeat_counts: dict[str, int] = {}
    next_depth: dict[str, int] = {}
    stuck = False
    ai: AIMessage
    while True:
        ai = _invoke_resilient(tools, messages)
        messages.append(ai)
        if not ai.tool_calls:
            break
        if calls >= MAX_CALLS or stuck:
            reason = ("Aynı sorguyu tekrarlıyorsun, yeni bilgi gelmiyor."
                      if stuck else "Arama bütçen doldu.")
            messages.append(HumanMessage(
                f"{reason} Daha fazla arama yapma; şimdiye kadar gördüklerinle "
                "SADECE JSON formatında karar ver."))
            ai = _invoke_resilient(None, messages, allow_tools=False)
            messages.append(ai)
            break
        for tc in ai.tool_calls:
            calls += 1
            args = tc.get("args") or {}
            q = args.get("query", "").strip().lower()
            if args.get("next"):
                n = next_depth.get(q, 0) + 1
                next_depth[q] = n
                if n > NEXT_DEPTH_LIMIT:
                    stuck = True
            else:
                n = repeat_counts.get(q, 0) + 1
                repeat_counts[q] = n
                if n >= 3:
                    stuck = True
            try:
                out = search_tool.invoke(tc["args"])
            except Exception as exc:
                out = f"HATA: {exc}"
            messages.append(ToolMessage(str(out), tool_call_id=tc["id"]))

    d = _parse_json_ladder(messages, ai.content,
                            bekleyen=("topic", "category", "subcategory"))
    if d is None:
        return None
    registry = store.load_registry()
    dup = (d.get("duplicate_of") or "").strip()
    if dup and not any(r["id"] == dup for r in registry):
        dup = ""                         # uydurma/geçersiz id -> yok say
    columns = d.get("columns") or table_data["columns"]
    rows = d.get("rows") or table_data["rows"]
    # SAHTE BANKA KORUMASI — build_row'daki AYNI güvence (synth.build_row):
    # finalize hiçbir zaman YENİ bir banka icat etmez, sadece var olanları
    # düzenler/sıkıştırır — bu yüzden geçerli anahtarlar SADECE table_data'nın
    # zaten bildiği bankalardır; model yanlışlıkla bir sütun adını (ya da başka
    # bir şeyi) üst seviye anahtar olarak döndürürse burada silinir.
    known_banks = set(table_data["rows"].keys())
    rows = {b: v for b, v in rows.items() if b in known_banks}
    # VERİ KAYBI YASAK — build_row'daki AYNI deterministik güvence (synth.
    # _restore_lost_values): sütun İSMİNİ değil VERİYİ korur. 'compact' derken
    # bir sütun (verisini taşıyarak) yeniden adlandırılmışsa eski isim GERİ
    # GELMEZ (hayalet sütun oluşmaz); gerçekten bir değer kaybolduysa gelir.
    _restore_lost_values(table_data["rows"], table_data["columns"], columns, rows)
    final_topic = (d.get("topic") or "").strip() or topic
    category = (d.get("category") or "").strip() or "ürün"
    subcategory = (d.get("subcategory") or "").strip() or "diğer"
    docstring = (d.get("docstring") or "").strip() or table_data.get("docstring", "")
    return {"duplicate_of": dup, "topic": final_topic, "category": category,
            "subcategory": subcategory, "docstring": docstring,
            "columns": columns, "rows": rows}


def finalize_table(topic: str, table_data: dict, subcats: list[str] | None = None) -> dict | None:
    """Tablo TAMAMEN kurulduktan SONRA, TEK seferde verilen kalıcı SON karar:
    sütunları gerekirse sıkılaştırır, mevcut havuzda gerçek bir mükerrer var mı
    SON kez kontrol eder, kategori + alt-kategori + tablo adı + docstring
    kararını verir. ASLA istisna fırlatmaz — hata olursa None döner (pipeline
    bu durumda taslak kategoriyle kaydeder, veri kaybetmez — bkz. pipeline.py)."""
    try:
        return _finalize_table(topic, table_data, subcats)
    except Exception as exc:
        log.error("  FINALIZE HATASI: %r: %s: %s", topic, type(exc).__name__, exc)
        return None
