"""Stable, user-checkable pages behind live bank values."""

LIVE_SOURCE_PAGES: dict[str, dict[str, tuple[str, str]]] = {
    "kuveytturk": {
        "default": (
            "https://www.kuveytturk.com.tr/hesaplama-araclari/",
            "Kuveyt Türk Hesaplama Araçları",
        ),
        "mile_earning_rates": (
            "https://milesandsmiles.kuveytturk.com.tr/",
            "Kuveyt Türk Miles&Smiles",
        ),
    },
    "albaraka": {
        "default": (
            "https://www.albaraka.com.tr/tr/hesaplama-araclari",
            "Albaraka Türk Hesaplama Araçları",
        ),
    },
    "vakif": {
        "default": (
            "https://www.vakifkatilim.com.tr/tr/yardimci-sayfalar/hesaplama-araclari",
            "Vakıf Katılım Hesaplama Araçları",
        ),
    },
    "emlak": {
        "default": (
            "https://www.emlakkatilim.com.tr/tr/hesaplama-araclari",
            "Emlak Katılım Hesaplama Araçları",
        ),
        "exchange_rates": (
            "https://www.emlakkatilim.com.tr/tr/tum-kurlarimiz",
            "Emlak Katılım Tüm Kurlarımız",
        ),
    },
    "dunya": {
        "default": (
            "https://dunyakatilim.com.tr/",
            "Dünya Katılım Hesaplama Araçları",
        ),
        "exchange_rates": (
            "https://dunyakatilim.com.tr/gunluk-kurlar",
            "Dünya Katılım Günlük Kurlar",
        ),
    },
    "ziraat": {
        "default": (
            "https://www.ziraatkatilim.com.tr/anasayfa",
            "Ziraat Katılım Hesaplama Araçları",
        ),
    },
    "turkiyefinans": {
        "default": (
            "https://www.turkiyefinans.com.tr/tr-tr/hesaplama-araclari/Sayfalar/hesaplama-araclari.aspx",
            "Türkiye Finans Hesaplama Araçları",
        ),
        "card_installment_quote": (
            "https://www.turkiyefinans.com.tr/tr-tr/hesaplama-araclari/Sayfalar/taksitle-hesaplama-araci.aspx",
            "Türkiye Finans Taksitle Hesaplama Aracı",
        ),
    },
    "hayat": {
        "default": ("https://hayatfinans.com.tr/", "Hayat Finans Hesaplama Araçları"),
    },
    "tom": {
        "default": (
            "https://www.tombank.com.tr/hesaplama-araclari.html",
            "T.O.M. Katılım Hesaplama Araçları",
        ),
    },
}


def live_source(bank: str, tool: str = "default") -> tuple[str, str]:
    """Return the public page for a live value, or an empty pair."""
    sources = LIVE_SOURCE_PAGES.get(bank, {})
    return sources.get(tool) or sources.get("default") or ("", "")
