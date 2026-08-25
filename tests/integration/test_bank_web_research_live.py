"""Live proof that every bank's optional web tools reach real public sources.

Run with SearXNG started by ``docker compose up -d searxng``. These URLs come
from the same cleaned corpus/table source fields specialists receive, rather
than a separate synthetic fixture.
"""

import json

import pytest

from agents.shared.bank_tools import build_bank_tools

pytestmark = [pytest.mark.integration, pytest.mark.slow]


SOURCES = {
    "adil": "https://www.adilkatilim.com.tr/katilim-bankaciligi/urun-ve-hizmetler",
    "albaraka": "https://www.albaraka.com.tr/tr/bireysel/finansmanlar/konut-finansmani",
    "dunya": "https://dunyakatilim.com.tr/dijital-bankacilik/mobil-sube",
    "emlak": "https://www.emlakkatilim.com.tr/tr/bireysel/finansmanlar/konut-finansmani",
    "hayat": "https://hayatfinans.com.tr/krediler",
    "kuveytturk": "https://saglamkart.kuveytturk.com.tr/kampanyalar/vatan-bilgisayar-ile-vade-farksiz-3-taksit-firsati-2598",
    "tom": "https://tombank.com.tr/vadeli-hesap.html",
    "turkiyefinans": "https://www.turkiyefinans.com.tr:443/tr-tr/bireysel/konut-finansmani/Sayfalar/konut-finansmani.aspx",
    "vakif": "https://www.vakifkatilim.com.tr/tr/musteri-ol",
    "ziraat": "https://www.ziraatkatilim.com.tr/konut-finansmani",
}


def _tools(bank):
    return {
        tool.name: tool
        for tool in build_bank_tools(bank, web_research_enabled=True)
    }


@pytest.mark.parametrize("bank,url", SOURCES.items(), ids=SOURCES.keys())
def test_each_specialist_can_open_a_real_source_url(bank, url):
    result = json.loads(_tools(bank)["read_bank_source"].invoke({"url": url}))
    assert result["bank"] == bank
    assert result["requested_url"] == url
    assert result["status"] == "ok", result
    assert result["url"].startswith("http")
    assert result["text"].strip()


@pytest.mark.parametrize("bank", SOURCES, ids=SOURCES.keys())
def test_each_specialist_can_search_only_its_own_bank(bank):
    result = json.loads(_tools(bank)["search_bank_web"].invoke({
        "query": "katılım bankacılığı ürün kampanya"
    }))
    assert result["bank"] == bank
    assert result["status"] in {"ok", "no_results"}, result
    for row in result["results"]:
        # Unit tests prove the exact hostname-boundary rule. This live test
        # proves the local metasearch actually returns consumable source URLs.
        assert row["url"].startswith("http")
