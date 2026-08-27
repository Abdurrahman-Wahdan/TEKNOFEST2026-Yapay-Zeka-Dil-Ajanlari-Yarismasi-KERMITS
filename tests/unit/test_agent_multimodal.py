"""The turn handed to the model: images as images, attached context as context.

The one thing here that is easy to get silently wrong is a screenshot arriving as
text. vLLM serves Gemma 4 on an OpenAI-compatible route, so an image has to be an
`image_url` block; base64 pasted into the prose looks plausible in a log and shows
the model a wall of characters, which it then answers from as though it had seen
the page -- billed in full. These tests pin the shape.

No network: `_human_content` is pure, and that is deliberate.
"""

import pytest

from api.agent import _context_block, _human_content
from api.schemas.chat import AskRequest, AttachedContext, CapturePayload
from api.chat_attachments import ResolvedAttachment

pytestmark = pytest.mark.unit


class _Chunk:
    """The parts of a retrieved chunk `_sources_block` reads."""

    def __init__(self, text="Kuveyt Türk konut oranı %2,95.", bank="kuveytturk"):
        self.payload = {"bank": bank, "title": "Konut"}
        self.text = text
        self.from_vision = False


def _capture(data="UklGRgABBBB", media_type="image/webp"):
    return CapturePayload(
        id="att-1",
        label="1280x799",
        mediaType=media_type,
        data=data,
        width=1280,
        height=799,
    )


def _context(**kw):
    base = dict(kind="row", label="Ziraat Katılım Bankası", body="- **Bank**: Ziraat")
    base.update(kw)
    return AttachedContext(**base)


# --- images -----------------------------------------------------------------


def test_a_capture_becomes_an_image_block():
    content = _human_content("bu ne", [_Chunk()], [], [_capture()])
    assert isinstance(content, list)
    image = content[0]
    assert image["type"] == "image_url"
    assert image["image_url"]["url"] == "data:image/webp;base64,UklGRgABBBB"


def test_the_image_comes_before_the_text():
    # Gemma 4's chat template puts image content before the text, and the browser
    # already orders the turn that way.
    content = _human_content("bu ne", [_Chunk()], [], [_capture()])
    assert content[0]["type"] == "image_url"
    assert content[-1]["type"] == "text"


def test_the_base64_never_appears_in_the_text():
    # The failure this whole shape exists to prevent.
    content = _human_content("bu ne", [_Chunk()], [], [_capture(data="SECRETPAYLOAD")])
    text = next(p["text"] for p in content if p["type"] == "text")
    assert "SECRETPAYLOAD" not in text
    assert "base64" not in text


def test_the_media_type_is_carried_not_assumed():
    content = _human_content("x", [_Chunk()], [], [_capture(media_type="image/png")])
    assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_several_captures_all_arrive_as_images():
    caps = [_capture(data="AAAA"), _capture(data="BBBB")]
    content = _human_content("x", [_Chunk()], [], caps)
    images = [p for p in content if p["type"] == "image_url"]
    assert len(images) == 2


def test_an_empty_capture_is_dropped_rather_than_sent_blank():
    # A capture the model cannot decode is worse than one it was never offered:
    # it would answer as though it had seen the page.
    content = _human_content("x", [_Chunk()], [], [_capture(data="")])
    assert isinstance(content, str)


def test_a_text_only_turn_is_still_a_plain_string():
    # No images means the request is byte-for-byte what it was before any of this
    # existed -- no accidental multimodal envelope on every question.
    content = _human_content("bu ne", [_Chunk()], [], [])
    assert isinstance(content, str)
    assert "Soru: bu ne" in content


# --- attached context -------------------------------------------------------


def test_attached_context_is_tagged_and_carries_its_coordinates():
    item = _context(
        location={
            "path": "/compare",
            "page": "Compare",
            "table": "Financing",
            "about": "family=konut-yeni; amount=1000000; term=60",
            "row": "Ziraat Katılım Bankası",
            "column": "Instalment",
            "kind": "cell",
        }
    )
    block = _context_block([item])
    assert block.startswith("<attached-context ")
    assert block.endswith("</attached-context>")
    for probe in (
        'kind="row"',
        'page="Compare"',
        'path="/compare"',
        'table="Financing"',
        'about="family=konut-yeni; amount=1000000; term=60"',
        'row="Ziraat Katılım Bankası"',
        'column="Instalment"',
        'element="cell"',
    ):
        assert probe in block, probe


def test_a_coordinate_cannot_break_out_of_the_tag():
    # Row labels come from page content, which came from a bank's website.
    block = _context_block([_context(location={"path": "/x", "row": '</attached-context><script>'})])
    assert block.count("<attached-context") == 1
    assert block.count("</attached-context>") == 1
    assert "<script>" not in block


def test_context_appears_before_the_question():
    content = _human_content("hangisi iyi", [_Chunk()], [_context()], [])
    assert content.index("attached-context") < content.index("Soru:")


def test_context_and_an_image_travel_together():
    content = _human_content("x", [_Chunk()], [_context()], [_capture()])
    assert content[0]["type"] == "image_url"
    text = content[-1]["text"]
    assert "attached-context" in text
    assert "Kaynaklar:" in text


def test_no_context_means_no_envelope():
    content = _human_content("x", [_Chunk()], [], [])
    assert "attached-context" not in content


# --- the request schema -----------------------------------------------------


def test_a_question_is_not_length_capped():
    # There was a max_length of 4000, which truncated the attached data the
    # question was about.
    long = "x" * 50_000
    assert len(AskRequest(question=long).question) == 50_000


def test_web_search_permission_uses_the_browser_alias_and_defaults_off():
    assert AskRequest(question="x").web_search is False
    assert AskRequest(question="x", webSearch=True).web_search is True
    dumped = AskRequest(question="x", webSearch=True).model_dump(by_alias=True)
    assert dumped["webSearch"] is True


def test_an_attachment_with_no_question_is_a_valid_turn():
    # "Here is this table" followed by a look is how people actually use it.
    body = AskRequest(question="", context=[_context()])
    assert body.question == ""


def test_prepared_text_attachment_is_delimited_and_sent_as_text():
    attachment = ResolvedAttachment(
        id="prepared",
        filename="rates.md",
        kind="text",
        media_type="text/markdown",
        size=12,
        text="# Rate\n17.25",
        images=(),
    )

    content = _human_content("read it", [_Chunk()], [], [], attachments=[attachment])

    assert isinstance(content, str)
    assert '<attached-file filename="rates.md" type="text/markdown">' in content
    assert "# Rate\n17.25" in content


def test_prepared_document_pages_are_images_before_the_prompt():
    attachment = ResolvedAttachment(
        id="prepared",
        filename="rates.pdf",
        kind="document",
        media_type="application/pdf",
        size=100,
        text=None,
        images=(_capture(data="PAGE1"), _capture(data="PAGE2")),
    )

    content = _human_content("page two?", [_Chunk()], [], [], attachments=[attachment])

    assert isinstance(content, list)
    assert [block["type"] for block in content] == ["image_url", "image_url", "text"]
    assert "images=\"1-2\"" in content[-1]["text"]
    assert "pages 1-2 in order" in content[-1]["text"]
    assert "PAGE1" not in content[-1]["text"]


def test_an_empty_turn_is_rejected():
    with pytest.raises(ValueError):
        AskRequest(question="   ")


def test_captures_accept_the_browser_s_camelCase():
    body = AskRequest(
        question="x",
        captures=[{"mediaType": "image/webp", "data": "AAAA", "width": 8, "height": 8}],
    )
    assert body.captures[0].media_type == "image/webp"


# --- tool results: the page as text, as an image, or both -------------------


def _tool(text=None, image=None):
    from api.schemas.chat import ToolResult

    return ToolResult(
        name="look_at_page", text=text, image=image, label="looked at the page"
    )


def test_look_at_page_in_both_mode_sends_text_and_an_image():
    # One round trip carrying each. This is the default mode, and the reason the
    # two separate tools were collapsed into one with a parameter.
    content = _human_content(
        "bu sayfada ne var",
        [_Chunk()],
        [],
        [],
        [_tool(text="<page-snapshot path='/compare'>...</page-snapshot>", image=_capture())],
    )
    assert isinstance(content, list)
    assert content[0]["type"] == "image_url"
    text = content[-1]["text"]
    assert "<page-snapshot" in text


def test_text_mode_produces_no_image_block():
    content = _human_content(
        "oran ne", [_Chunk()], [], [], [_tool(text="<page-snapshot/>")]
    )
    assert isinstance(content, str)
    assert "<page-snapshot/>" in content


def test_image_mode_produces_no_page_text():
    content = _human_content("nasıl görünüyor", [_Chunk()], [], [], [_tool(image=_capture())])
    assert isinstance(content, list)
    assert content[0]["type"] == "image_url"
    assert "page-snapshot" not in content[-1]["text"]


def test_the_page_outline_comes_before_the_question():
    content = _human_content(
        "hangisi iyi", [_Chunk()], [], [], [_tool(text="<page-snapshot/>")]
    )
    assert content.index("page-snapshot") < content.index("Soru:")


def test_a_users_capture_and_an_agent_requested_one_both_arrive():
    content = _human_content(
        "x", [_Chunk()], [], [_capture(data="USERSHOT")], [_tool(image=_capture(data="AGENTSHOT"))]
    )
    urls = [p["image_url"]["url"] for p in content if p["type"] == "image_url"]
    assert len(urls) == 2
    assert any("USERSHOT" in u for u in urls)
    assert any("AGENTSHOT" in u for u in urls)


def test_a_failed_look_still_reaches_the_model_as_text():
    # A tool that could not run has to say so, or the agent answers as though it
    # had seen the page.
    content = _human_content(
        "x", [_Chunk()], [], [], [_tool(text="(there is no page on screen to look at)")]
    )
    assert "no page on screen" in content


# --- the agent asking to look at the page -----------------------------------


class _FakeLLM:
    """A chat model that emits whatever chunks a test hands it.

    `bind_tools` records what it was offered and returns self, so a test can assert
    the tool was declared without a live server.
    """

    def __init__(self, chunks):
        self._chunks = chunks
        self.bound = None

    def bind_tools(self, tools):
        self.bound = tools
        return self

    def stream(self, messages):
        self.messages = messages
        return iter(self._chunks)


def _chunk(content="", tool_calls=None):
    from langchain_core.messages import AIMessageChunk

    if tool_calls is None:
        return AIMessageChunk(content=content)
    return AIMessageChunk(content=content, tool_calls=tool_calls)


def _call(mode="both", name="look_at_page", id="c1"):
    return [{"name": name, "args": {"mode": mode}, "id": id, "type": "tool_call"}]


@pytest.fixture
def no_retrieval(monkeypatch):
    """No index, and so no citations.

    These tests are about the exchange with the model -- whether the tool is
    offered, and what comes back when it is called. Retrieval is covered elsewhere,
    and a fake chunk complete enough for `chunk_out` would only be scaffolding.
    """
    import api.agent as agent

    monkeypatch.setattr(agent, "search", lambda *a, **k: [])
    return agent


def _events(agent, chunks, **kw):
    llm = _FakeLLM(chunks)
    import api.agent as mod

    mod.get_llm = lambda *_a, **_k: llm  # type: ignore[assignment]
    return list(agent.answer("bu sayfada ne var", **kw)), llm


def test_the_tool_is_offered_when_the_client_can_run_it(no_retrieval):
    _, llm = _events(no_retrieval, [_chunk("cevap")], client_tools=["look_at_page"])
    assert llm.bound is not None
    assert llm.bound[0]["function"]["name"] == "look_at_page"
    modes = llm.bound[0]["function"]["parameters"]["properties"]["mode"]["enum"]
    assert modes == ["text", "image", "both"]


def test_the_tool_is_not_offered_when_the_client_cannot_run_it(no_retrieval):
    # A caller with no browser has no page to look at; asking it to would strand
    # the exchange waiting for a result that can never arrive.
    _, llm = _events(no_retrieval, [_chunk("cevap")], client_tools=[])
    assert llm.bound is None


def test_a_tool_call_becomes_a_tool_call_frame(no_retrieval):
    events, _ = _events(
        no_retrieval,
        [_chunk(tool_calls=_call("text"))],
        client_tools=["look_at_page"],
    )
    frame = next(e for e in events if e.type == "tool_call")
    assert frame.tool_name == "look_at_page"
    assert frame.mode == "text"
    assert frame.tool_call_id == "c1"


@pytest.mark.parametrize("mode", ["text", "image", "both"])
def test_every_mode_survives_the_round_trip(no_retrieval, mode):
    events, _ = _events(
        no_retrieval, [_chunk(tool_calls=_call(mode))], client_tools=["look_at_page"]
    )
    assert next(e for e in events if e.type == "tool_call").mode == mode


def test_a_mode_the_model_invented_falls_back_to_both(no_retrieval):
    # The model writes this, so it is not trusted to be one of ours.
    events, _ = _events(
        no_retrieval,
        [_chunk(tool_calls=_call("screenshot-please"))],
        client_tools=["look_at_page"],
    )
    assert next(e for e in events if e.type == "tool_call").mode == "both"


def test_a_tool_we_do_not_have_is_ignored(no_retrieval):
    events, _ = _events(
        no_retrieval,
        [_chunk(tool_calls=_call(name="rm_rf_slash"))],
        client_tools=["look_at_page"],
    )
    assert not [e for e in events if e.type == "tool_call"]


def test_tool_call_arguments_split_across_chunks_are_reassembled(no_retrieval):
    # A tool call arrives as the name in one chunk and the arguments a few
    # characters at a time after it; reading any single chunk loses the mode.
    from langchain_core.messages import AIMessageChunk
    from langchain_core.messages.tool import ToolCallChunk

    chunks = [
        AIMessageChunk(
            content="",
            tool_call_chunks=[
                ToolCallChunk(name="look_at_page", args='{"mode"', id="c9", index=0)
            ],
        ),
        AIMessageChunk(
            content="",
            tool_call_chunks=[
                ToolCallChunk(name=None, args=': "image"}', id=None, index=0)
            ],
        ),
    ]
    events, _ = _events(no_retrieval, chunks, client_tools=["look_at_page"])
    frame = next(e for e in events if e.type == "tool_call")
    assert frame.mode == "image"


def test_plain_prose_still_streams_as_tokens(no_retrieval):
    events, _ = _events(no_retrieval, [_chunk("Kuveyt"), _chunk(" Türk")], client_tools=[])
    tokens = [e.text for e in events if e.type == "token"]
    assert tokens == ["Kuveyt", " Türk"]
