"""Compaction: what it reads, what it keeps, and how it fails.

No network — the summarising model is a stub. Every test here corresponds to a
way the upstream defaults lose conversation history silently.
"""

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict, Field

from agents.shared.compaction import (
    SUMMARY_PROMPT,
    SummaryUnavailable,
    ThreadCompaction,
    build_compaction,
)
from config.settings import settings

pytestmark = pytest.mark.unit


class StubModel(BaseChatModel):
    """A summariser that returns what it is told to, and records what it saw.

    A real BaseChatModel rather than a bare object: the middleware reads
    `_llm_type` to tune its token counter, so a duck-typed stub never gets far
    enough to be useful.
    """

    reply: str = "a summary"
    error: Exception | None = None
    prompts: list[str] = Field(default_factory=list)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def _llm_type(self) -> str:
        return "stub"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.prompts.append(
            "\n".join(
                m.content if isinstance(m.content, str) else str(m.content)
                for m in messages
            )
        )
        if self.error is not None:
            raise self.error
        return ChatResult(generations=[ChatGeneration(message=AIMessage(self.reply))])


def _middleware(model=None, budget=0, keep=10) -> ThreadCompaction:
    mw = ThreadCompaction(
        model=model or StubModel(),
        trigger=("tokens", 1000),
        keep=("messages", keep),
        summary_prompt=SUMMARY_PROMPT,
        trim_tokens_to_summarize=None,
    )
    mw.summary_input_budget = budget
    return mw


def _thread(n: int) -> list:
    out = []
    for i in range(n):
        out.append(HumanMessage(f"user turn {i}"))
        out.append(AIMessage(f"assistant turn {i}"))
    return out


# ----- no truncation -----------------------------------------------------


def test_the_summariser_reads_the_whole_thread():
    """Upstream shows it only the last 4000 tokens. Every message must appear."""
    model = StubModel()
    mw = _middleware(model)
    mw._create_summary(_thread(40))
    prompt = model.prompts[0]
    for i in range(40):
        assert f"user turn {i}" in prompt
        assert f"assistant turn {i}" in prompt


def test_trimming_is_off_by_construction():
    """A regression guard on the 4000-token default, which is silent when wrong."""
    mw = build_compaction(100_000, specialist=False)
    assert mw.trim_tokens_to_summarize is None


def test_the_trimming_helper_is_never_called(monkeypatch):
    """It falls back to messages[-15:] on any error -- a silent tail slice."""
    mw = _middleware()

    def forbidden(*args, **kwargs):
        raise AssertionError("_trim_messages_for_summary must not be used")

    monkeypatch.setattr(mw, "_trim_messages_for_summary", forbidden)
    mw._create_summary(_thread(5))


# ----- failure must not destroy the thread -------------------------------


def test_a_failing_summariser_raises_instead_of_summarising_the_error():
    """Upstream returns "Error generating summary: ..." *as the summary*, and the
    middleware has already emitted REMOVE_ALL_MESSAGES by then."""
    mw = _middleware(StubModel(error=RuntimeError("tunnel died")))
    with pytest.raises(RuntimeError, match="tunnel died"):
        mw._create_summary(_thread(3))


def test_an_empty_summary_is_refused():
    """Replacing a conversation with nothing is worse than not compacting."""
    mw = _middleware(StubModel(reply="   "))
    with pytest.raises(SummaryUnavailable, match="returned nothing"):
        mw._create_summary(_thread(3))


def test_summarising_nothing_is_refused():
    with pytest.raises(SummaryUnavailable, match="Nothing to summarise"):
        _middleware()._create_summary([])


# ----- language ----------------------------------------------------------


def test_the_prompt_asks_for_the_conversation_s_language():
    """Measured: the upstream prompt turned a Turkish thread into English."""
    assert "same language the conversation is in" in SUMMARY_PROMPT
    assert "Turkish" in SUMMARY_PROMPT


def test_the_summary_is_framed_without_english_prose():
    """Upstream wraps it in "Here is a summary of the conversation to date:"."""
    [message] = ThreadCompaction._build_new_messages("özet metni")
    assert "<conversation_summary>" in message.content
    assert "özet metni" in message.content
    assert "Here is a summary" not in message.content
    assert message.additional_kwargs["lc_source"] == "summarization"


# ----- folding a thread too long for one pass ----------------------------


def test_a_thread_within_budget_is_one_pass():
    mw = _middleware(budget=1_000_000)
    assert len(mw._fit_to_budget(_thread(10))) == 1


def test_an_oversized_thread_is_split_on_message_boundaries():
    """Nothing is cut: every message lands whole, in exactly one group."""
    messages = _thread(30)
    mw = _middleware(budget=200)
    groups = mw._fit_to_budget(messages)
    assert len(groups) > 1
    assert [m for group in groups for m in group] == messages


def test_every_group_fits_the_budget():
    mw = _middleware(budget=200)
    for group in mw._fit_to_budget(_thread(30)):
        assert mw.token_counter(group) <= 200


def test_folding_summarises_each_group_then_the_summaries():
    model = StubModel()
    mw = _middleware(model, budget=200)
    groups = mw._fit_to_budget(_thread(30))
    mw._create_summary(_thread(30))
    # One call per group, plus the fold.
    assert len(model.prompts) == len(groups) + 1


def test_a_single_message_larger_than_the_budget_is_refused():
    """The one case that cannot be handled without cutting content."""
    mw = _middleware(budget=50)
    with pytest.raises(SummaryUnavailable, match="without cutting"):
        mw._fit_to_budget([HumanMessage("word " * 500)])


# ----- the two tiers -----------------------------------------------------


def test_the_two_tiers_read_their_own_settings(monkeypatch):
    monkeypatch.setattr(settings, "COMPACT_AT_FRACTION", 0.5)
    monkeypatch.setattr(settings, "COMPACT_SPECIALIST_AT_FRACTION", 0.9)
    monkeypatch.setattr(settings, "COMPACT_KEEP_MESSAGES", 4)
    monkeypatch.setattr(settings, "COMPACT_SPECIALIST_KEEP_MESSAGES", 20)

    main = build_compaction(100_000, specialist=False)
    specialist = build_compaction(100_000, specialist=True)

    assert main._trigger_clauses == [{"tokens": 50_000}]
    assert specialist._trigger_clauses == [{"tokens": 90_000}]
    assert main.keep == ("messages", 4)
    assert specialist.keep == ("messages", 20)


def test_the_threshold_is_a_fraction_of_the_window_it_is_given():
    """Not of the reported window: the difference is the uncompactable floor."""
    mw = build_compaction(10_000, specialist=False)
    assert mw._trigger_clauses == [
        {"tokens": int(10_000 * settings.COMPACT_AT_FRACTION)}
    ]


# ----- attachment ---------------------------------------------------------
#
# `create_agent` returns a compiled graph that does not expose the middleware it
# was given, so these capture the call instead of inspecting the result.


def _capture(monkeypatch, module):
    """Record the kwargs the module passes to create_agent."""
    seen: dict = {}

    def fake_create_agent(**kwargs):
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(module, "create_agent", fake_create_agent)
    monkeypatch.setattr(module, "get_checkpointer", lambda: object())
    monkeypatch.setattr(module, "usable_context_window", lambda *a, **k: 100_000)
    return seen


def test_the_supervisor_is_built_with_compaction(monkeypatch):
    from agents.main import agent as main_agent

    seen = _capture(monkeypatch, main_agent)
    monkeypatch.setattr(main_agent, "build_specialist_tools", lambda: [])
    monkeypatch.setattr(main_agent, "get_llm", lambda *a, **k: StubModel())
    main_agent.build_main_agent()

    compaction = [m for m in seen["middleware"] if isinstance(m, ThreadCompaction)]
    assert len(compaction) == 1
    assert compaction[0]._trigger_clauses == [
        {"tokens": int(100_000 * settings.COMPACT_AT_FRACTION)}
    ]


def test_the_supervisor_asks_for_usage_while_streaming(monkeypatch):
    """Without stream_usage the streamed answer carries no token counts at all."""
    from agents.main import agent as main_agent

    _capture(monkeypatch, main_agent)
    monkeypatch.setattr(main_agent, "build_specialist_tools", lambda: [])
    kwargs: dict = {}

    def spy_get_llm(*args, **kw):
        kwargs.update(kw)
        return StubModel()

    monkeypatch.setattr(main_agent, "get_llm", spy_get_llm)
    main_agent.build_main_agent()
    assert kwargs.get("stream_usage") is True


def test_every_specialist_is_built_with_compaction(monkeypatch):
    """All ten threads are compacted, not just the one the user can see."""
    from agents.shared import specialists

    seen = _capture(monkeypatch, specialists)
    monkeypatch.setattr(specialists, "build_bank_tools", lambda *a, **k: [])
    monkeypatch.setattr(specialists, "get_llm", lambda *a, **k: StubModel())
    specialists.build_specialist("kuveytturk")

    compaction = [m for m in seen["middleware"] if isinstance(m, ThreadCompaction)]
    assert len(compaction) == 1
    assert compaction[0]._trigger_clauses == [
        {"tokens": int(100_000 * settings.COMPACT_SPECIALIST_AT_FRACTION)}
    ]
    assert compaction[0].keep == ("messages", settings.COMPACT_SPECIALIST_KEEP_MESSAGES)


def test_the_specialist_window_is_measured_from_its_own_prompt_and_tools(monkeypatch):
    """Not the supervisor's: "70% full" has to mean 70% of *this* agent's room."""
    from agents.shared import specialists

    calls: list = []

    def fake_window(model_key, system_prompt, tools):
        calls.append((model_key, system_prompt, tools))
        return 100_000

    monkeypatch.setattr(specialists, "create_agent", lambda **kw: object())
    monkeypatch.setattr(specialists, "get_checkpointer", lambda: object())
    monkeypatch.setattr(specialists, "usable_context_window", fake_window)
    monkeypatch.setattr(specialists, "build_bank_tools", lambda *a, **k: ["a tool"])
    monkeypatch.setattr(specialists, "get_llm", lambda *a, **k: StubModel())
    specialists.build_specialist("kuveytturk")

    [(_, system_prompt, tools)] = calls
    assert tools == ["a tool"]
    assert system_prompt, "the specialist's own prompt must be measured"
