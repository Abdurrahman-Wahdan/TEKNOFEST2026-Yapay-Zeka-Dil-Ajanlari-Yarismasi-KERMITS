"""Compaction against the running vLLM host.

Everything here talks to the real model. Skipped when the host is unreachable,
because a tunnel that is down is not a test failure.

What these cover that the unit tests cannot: the window the server actually
reports, and whether a real model writes the summary in the conversation's
language and keeps the figures in it.
"""

import pytest
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from agents.shared.compaction import SUMMARY_PROMPT, ThreadCompaction, build_compaction
from config.settings import settings
from llm import get_llm
from llm.context import fixed_overhead, reported_context_window, usable_context_window
from llm.providers.vllm_provider import MODELS

pytestmark = [pytest.mark.integration, pytest.mark.slow]

CHAT = settings.CHAT_MODEL


@pytest.fixture(scope="module")
def host() -> None:
    try:
        reported_context_window(CHAT)
    except Exception as exc:
        pytest.skip(f"vLLM host unreachable: {exc}")


TURKISH = [
    HumanMessage("Merhaba, 500.000 TL tutarında yeni konut finansmanı almak istiyorum."),
    AIMessage("Tabii. Vade olarak kaç ay düşünüyorsunuz?"),
    HumanMessage("36 ay olsun. Kuveyt Türk ve Albaraka'yı karşılaştırır mısın?"),
    AIMessage(
        "Kuveyt Türk aylık %2,89 kâr payı oranı veriyor, Albaraka %3,05. "
        "36 ay vadede Kuveyt Türk'ün taksiti daha düşük çıkıyor."
    ),
    HumanMessage("Peki 48 ay olsaydı ne olurdu?"),
]

ENGLISH = [
    HumanMessage("I want 500,000 TRY of new-home financing."),
    AIMessage("Certainly. What term are you considering?"),
    HumanMessage("36 months. Compare Kuveyt Türk and Albaraka please."),
    AIMessage(
        "Kuveyt Türk offers a 2.89% monthly profit rate and Albaraka 3.05%. "
        "Over 36 months Kuveyt Türk's instalment is lower."
    ),
    HumanMessage("What about 48 months?"),
]


def _compaction(budget: int = 0) -> ThreadCompaction:
    mw = ThreadCompaction(
        model=get_llm(settings.COMPACT_MODEL),
        trigger=("tokens", 1_000_000),
        keep=("messages", settings.COMPACT_KEEP_MESSAGES),
        summary_prompt=SUMMARY_PROMPT,
        trim_tokens_to_summarize=None,
    )
    mw.summary_input_budget = budget
    return mw


# ----- the window --------------------------------------------------------


def test_the_server_reports_a_window(host):
    """The number the whole design divides by."""
    assert reported_context_window(CHAT) > 0


def test_the_reported_window_is_what_the_constant_claims_or_the_constant_is_stale(host):
    """Not an equality assertion: it is a report.

    MODELS is a written-down measurement and the server is the truth. If these
    disagree the constant has drifted, which is the failure this module exists
    to make visible rather than a reason to fail the suite.
    """
    reported = reported_context_window(CHAT)
    written = MODELS[CHAT].context_window
    assert reported == written, (
        f"MODELS[{CHAT!r}].context_window is {written}, the host serves {reported}. "
        "Update the constant; the live value is the one in use."
    )


def test_the_usable_window_leaves_room_for_the_floor(host):
    from agents.main.prompt import NAME
    from agents.shared.agent_tools import build_specialist_tools

    tools = build_specialist_tools()
    reported = reported_context_window(CHAT)
    usable = usable_context_window(CHAT, NAME, tools)
    assert usable < reported
    assert reported - usable == fixed_overhead(NAME, tools)


# ----- language ----------------------------------------------------------


def test_a_turkish_conversation_is_summarised_in_turkish(host):
    """LangChain's default prompt returned English here. Ours must not."""
    summary = _compaction()._create_summary(TURKISH)
    turkish = sum(
        summary.lower().count(word)
        for word in ("finansman", "vade", "kâr", "taksit", "oran", "ay")
    )
    assert turkish >= 3, f"summary does not read as Turkish:\n{summary}"
    assert "Here is a summary" not in summary


def test_an_english_conversation_is_summarised_in_english(host):
    """The instruction follows the conversation, not a pinned locale."""
    summary = _compaction()._create_summary(ENGLISH)
    english = sum(
        summary.lower().count(word)
        for word in ("financing", "month", "rate", "instalment", "installment", "compare")
    )
    assert english >= 2, f"summary does not read as English:\n{summary}"


def test_the_summary_keeps_the_figures(host):
    """A summary that loses the amounts has lost the conversation."""
    summary = _compaction()._create_summary(TURKISH)
    assert "500" in summary
    assert "36" in summary
    for rate in ("2,89", "2.89"):
        if rate in summary:
            break
    else:
        pytest.fail(f"the profit rate did not survive the summary:\n{summary}")


# ----- folding -----------------------------------------------------------


def test_a_thread_too_long_for_one_pass_is_folded_not_cut(host):
    """Every group is summarised and the summaries are summarised.

    Driven by a deliberately tiny budget rather than a real 100k thread: the
    behaviour under test is the folding, and a real one would take minutes to
    prove the same thing.
    """
    # 50: the thread is ~103 tokens and its largest single message is 35, so
    # this forces several groups while no message is too big to summarise.
    mw = _compaction(budget=50)
    groups = mw._fit_to_budget(TURKISH)
    assert len(groups) > 1, "budget too generous to exercise folding"
    # Nothing is dropped on the way into the groups.
    assert [m for group in groups for m in group] == TURKISH
    summary = mw._create_summary(TURKISH)
    assert summary.strip()


# ----- inside a real agent ----------------------------------------------


def test_compaction_fires_in_a_real_graph_and_keeps_the_tail(host):
    """The middleware, a real model, a real checkpointer, a real conversation."""
    keep = 2
    mw = ThreadCompaction(
        model=get_llm(settings.COMPACT_MODEL),
        # Low enough that the second turn crosses it.
        trigger=("tokens", 60),
        keep=("messages", keep),
        summary_prompt=SUMMARY_PROMPT,
        trim_tokens_to_summarize=None,
    )
    mw.summary_input_budget = 0

    agent = create_agent(
        model=get_llm(settings.COMPACT_MODEL),
        tools=[],
        system_prompt="Answer in one short sentence.",
        checkpointer=InMemorySaver(),
        middleware=[mw],
    )
    config = {"configurable": {"thread_id": "compaction-live"}}

    for question in (
        "500.000 TL konut finansmanı istiyorum.",
        "Vade 36 ay olsun.",
        "Kuveyt Türk'ü sor.",
    ):
        agent.invoke({"messages": [("user", question)]}, config=config)

    messages = agent.get_state(config).values["messages"]
    summaries = [
        m for m in messages
        if m.additional_kwargs.get("lc_source") == "summarization"
    ]
    assert summaries, "compaction never fired"
    assert "<conversation_summary>" in summaries[0].content
    assert "Here is a summary of the conversation to date" not in summaries[0].content


def test_the_supervisor_streams_with_usage(host):
    """Streaming carries no usage unless asked; the ring depends on this."""
    model = get_llm(settings.COMPACT_MODEL, stream_usage=True)
    usage = None
    for chunk in model.stream("Say OK.", max_tokens=8):
        if getattr(chunk, "usage_metadata", None):
            usage = chunk.usage_metadata
    assert usage is not None, "no usage on the streaming path"
    assert usage["input_tokens"] > 0


def test_a_history_reseeded_in_one_turn_is_compacted_before_the_model(host):
    """The path that reloads a conversation whose checkpoints are gone.

    `_agent_answer` seeds the whole stored history when the thread is empty. It
    does not shorten it -- this is what makes that safe: the middleware runs in
    `before_model`, so an oversized history is summarised before the model is
    called rather than sent whole and rejected.
    """
    keep = 2
    mw = ThreadCompaction(
        model=get_llm(settings.COMPACT_MODEL),
        trigger=("tokens", 60),
        keep=("messages", keep),
        summary_prompt=SUMMARY_PROMPT,
        trim_tokens_to_summarize=None,
    )
    mw.summary_input_budget = 0

    agent = create_agent(
        model=get_llm(settings.COMPACT_MODEL),
        tools=[],
        system_prompt="Answer in one short sentence.",
        checkpointer=InMemorySaver(),
        middleware=[mw],
    )
    config = {"configurable": {"thread_id": "reseed-live"}}

    # A whole stored conversation arriving at once, as the reseed path sends it.
    seeded = [*TURKISH, HumanMessage("Özetle.")]
    agent.invoke({"messages": seeded}, config=config)

    messages = agent.get_state(config).values["messages"]
    summaries = [
        m for m in messages
        if m.additional_kwargs.get("lc_source") == "summarization"
    ]
    assert summaries, "an oversized reseeded history was not compacted"
    assert "<conversation_summary>" in summaries[0].content


def test_compacting_by_hand_summarises_and_keeps_the_tail(host):
    """The manual path: same code as the automatic one, threshold bypassed.

    Runs on a thread of its own rather than a real conversation -- compaction
    rewrites the thread it touches, and a test must not do that to a user's.
    """
    keep = 4
    mw = ThreadCompaction(
        # A threshold far above anything here: if compaction happens, it is
        # because it was asked for, not because the thread grew.
        model=get_llm(settings.COMPACT_MODEL),
        trigger=("tokens", 10_000_000),
        keep=("messages", keep),
        summary_prompt=SUMMARY_PROMPT,
        trim_tokens_to_summarize=None,
    )
    mw.summary_input_budget = 0

    agent = create_agent(
        model=get_llm(settings.COMPACT_MODEL),
        tools=[],
        system_prompt="Answer in one short sentence.",
        checkpointer=InMemorySaver(),
        middleware=[mw],
    )
    config = {"configurable": {"thread_id": "manual-compaction-live"}}
    # Distinct messages, not TURKISH * 3: the messages reducer keys on id, and
    # repeating the same objects collapses back to five.
    thread = [*TURKISH]
    for i in range(5):
        thread.append(HumanMessage(f"{i}. soru: vade {24 + i * 6} ay olursa?"))
        thread.append(AIMessage(f"{24 + i * 6} ay vadede taksit yaklaşık {20 - i} bin TL."))
    agent.update_state(config, {"messages": thread})

    before = agent.get_state(config).values["messages"]
    assert len(before) == len(thread)

    update = mw.compact_now(agent.get_state(config).values)
    assert update is not None, "nothing was compacted"
    agent.update_state(config, update)

    after = agent.get_state(config).values["messages"]
    summaries = [m for m in after if m.additional_kwargs.get("lc_source") == "summarization"]
    assert summaries, "no summary was written"
    assert len(after) < len(before), "the thread did not shrink"
    # The tail survives verbatim -- the last `keep` messages are the originals.
    assert [m.content for m in after[-keep:]] == [m.content for m in before[-keep:]]
    # Everything ahead of the tail is now one message.
    assert len(after) == keep + 1

    # Deliberately *not* asserting that the token count fell. On a short thread
    # it rises: a careful summary of 233 tokens of conversation can be longer
    # than the conversation. Compaction only saves tokens once the history is
    # large, which is the only time it fires on its own. The manual path can be
    # used below that, and then it costs rather than saves -- worth knowing
    # before wiring it to a button.


def test_measure_reports_the_threshold_the_middleware_will_actually_use(host):
    """One definition of "70% full": the ring and the trigger share it."""
    from agents.shared.compaction import measure

    mw = build_compaction(100_000, specialist=False)
    level = measure(mw, TURKISH, 100_000)
    assert level.compact_at_tokens == int(100_000 * settings.COMPACT_AT_FRACTION)
    assert level.used_tokens == mw.token_counter(TURKISH)
    assert level.usable_tokens == 100_000
    assert 0.0 <= level.fraction <= 1.0
