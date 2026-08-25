"""The optional live-web surface is bank-bound and evidence preserving."""

import asyncio
import json

import pytest

from agents.shared import web_research
from agents.shared.bank_tools import build_bank_tools
from agents.shared.registry import SPECS

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def production_reader_default(monkeypatch):
    """Unit coverage stays on the production default despite local test mode."""
    monkeypatch.setattr(web_research.settings, "WEB_READ_SOURCE_ENABLED", True)


def _tool(bank: str, name: str):
    return next(
        tool for tool in build_bank_tools(bank, web_research_enabled=True)
        if tool.name == name
    )


def test_web_tools_exist_only_when_the_request_permits_them():
    for spec in SPECS:
        disabled = {tool.name for tool in build_bank_tools(spec.bank)}
        enabled = {
            tool.name
            for tool in build_bank_tools(spec.bank, web_research_enabled=True)
        }
        assert "search_bank_web" not in disabled
        assert "read_bank_source" not in disabled
        assert {"search_bank_web", "read_bank_source"} <= enabled


def test_search_only_assessment_removes_reader_from_the_schema():
    for spec in SPECS:
        names = {
            tool.name
            for tool in web_research.build_bank_web_tools(
                spec.bank, include_reader=False
            )
        }
        assert names == {"search_bank_web"}


def test_web_tool_schemas_never_expose_a_bank_or_domain_selector():
    for spec in SPECS:
        for name in ("search_bank_web", "read_bank_source"):
            properties = _tool(spec.bank, name).args_schema.model_json_schema()["properties"]
            assert "bank" not in properties
            assert "domain" not in properties


def test_exact_reader_refuses_another_banks_url_before_network_access():
    result = json.loads(_tool("vakif", "read_bank_source").invoke({
        "url": "https://www.albaraka.com.tr/tr/bireysel"
    }))
    assert result["status"] == "error"
    assert "vakifkatilim.com.tr" in result["message"]


def test_reader_extracts_html_into_a_timestamped_evidence_envelope(monkeypatch):
    html = b"""
        <html lang='tr'><head><title>Konut Finansmani</title></head>
        <body><main><h1>Konut Finansmani</h1><p>Azami vade 120 aydir.</p></main></body></html>
    """
    monkeypatch.setattr(
        web_research,
        "_download",
        lambda site, url: (html, url, "text/html", 200),
    )
    result = json.loads(_tool("vakif", "read_bank_source").invoke({
        "url": "https://www.vakifkatilim.com.tr/tr/konut-finansmani"
    }))
    assert result["status"] == "ok"
    assert result["source_type"] == "live_web_page"
    assert result["url"].startswith("https://www.vakifkatilim.com.tr/")
    assert result["retrieved_at"].endswith("+00:00")
    assert "120" in result["text"]


def test_reader_marks_fake_200_not_found_pages_unavailable(monkeypatch):
    html = b"<html><head><title>Sayfa Bulunamadi</title></head><body>Page not found</body></html>"
    monkeypatch.setattr(
        web_research,
        "_download",
        lambda site, url: (html, "https://www.vakifkatilim.com.tr/tr/404", "text/html", 200),
    )
    result = json.loads(_tool("vakif", "read_bank_source").invoke({
        "url": "https://www.vakifkatilim.com.tr/tr/missing"
    }))
    assert result["status"] == "unavailable"


def test_search_filters_out_other_banks_and_untrusted_results(monkeypatch):
    requests = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": [
                {"title": "Right", "url": "https://www.vakifkatilim.com.tr/tr/a", "content": "A"},
                {"title": "Other bank", "url": "https://www.albaraka.com.tr/tr/b", "content": "B"},
                {"title": "Lookalike", "url": "https://vakifkatilim.com.tr.evil.test/c", "content": "C"},
            ]}

    def fake_get(*args, **kwargs):
        requests.append(kwargs["params"])
        return Response()

    monkeypatch.setattr(web_research.httpx, "get", fake_get)
    result = json.loads(_tool("vakif", "search_bank_web").invoke({"query": "konut vade"}))
    assert result["status"] == "ok"
    assert [row["title"] for row in result["results"]] == ["Right"]
    assert "snippets are discovery hints" in result["note"]
    assert requests[0]["q"] == "Vakıf Katılım konut vade"
    assert "site:" not in requests[0]["q"]


def test_search_does_not_duplicate_a_bank_name_already_in_the_query(monkeypatch):
    requests = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": []}

    def fake_get(*args, **kwargs):
        requests.append(kwargs["params"])
        return Response()

    monkeypatch.setattr(web_research.httpx, "get", fake_get)
    query = "Vakıf Katılım konut finansmanı başvuru"
    _tool("vakif", "search_bank_web").invoke({"query": query})
    assert requests[0]["q"] == query


def test_transient_all_engine_failure_is_explicit_and_never_cached(monkeypatch):
    calls = 0

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [],
                "unresponsive_engines": [
                    ["bing", "Suspended: too many requests"],
                    ["duckduckgo web", "CAPTCHA"],
                ],
            }

    def fake_get(*args, **kwargs):
        nonlocal calls
        calls += 1
        return Response()

    monkeypatch.setattr(web_research.httpx, "get", fake_get)
    tool = _tool("kuveytturk", "search_bank_web")
    first = json.loads(tool.invoke({"query": "sukuk ürünleri"}))
    second = json.loads(tool.invoke({"query": "sukuk ürünleri"}))

    assert first["status"] == "search_unavailable"
    assert second["status"] == "search_unavailable"
    assert "Every selected upstream" in first["message"]
    assert calls == 2


def test_async_tool_path_uses_the_same_bank_bound_reader(monkeypatch):
    monkeypatch.setattr(
        web_research,
        "_download",
        lambda site, url: (
            "<html><body><h1>Hesap</h1><p>Katılma hesabı bilgisi.</p></body></html>".encode(),
            url,
            "text/html",
            200,
        ),
    )

    async def run():
        return await _tool("kuveytturk", "read_bank_source").ainvoke({
            "url": "https://www.kuveytturk.com.tr/hesap"
        })

    result = json.loads(asyncio.run(run()))
    assert result["status"] == "ok"
    assert result["bank"] == "kuveytturk"


def test_reader_routes_bank_hosted_images_to_vision(monkeypatch):
    monkeypatch.setattr(
        web_research,
        "_download",
        lambda site, url: (b"fake-image", url, "image/png", 200),
    )
    monkeypatch.setattr(
        web_research,
        "_image_text",
        lambda data, content_type: ("Kampanya son tarihi: 31.12.2026", "vision_model"),
    )
    result = json.loads(_tool("albaraka", "read_bank_source").invoke({
        "url": "https://www.albaraka.com.tr/assets/kampanya.png"
    }))
    assert result["status"] == "ok"
    assert result["source_type"] == "live_web_image"
    assert result["extraction_engine"] == "vision_model"
