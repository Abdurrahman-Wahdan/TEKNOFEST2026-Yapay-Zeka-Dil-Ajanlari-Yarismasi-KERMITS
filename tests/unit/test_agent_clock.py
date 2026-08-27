"""The clock the agents are told, and the clock they are handed in tool results.

Both halves, in one file, because they are one bug: an agent that knows the date
but quotes a UTC `retrieved_at` still tells the user the wrong time, and an agent
handed a correct timestamp with no idea what day it is still cannot answer
"bugün".
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from agents.shared.clock import EN_DAYS, TR_DAYS, TR_MONTHS, now_block

TZ = ZoneInfo("Europe/Istanbul")


def test_the_block_states_the_istanbul_wall_clock():
    # 06:30 UTC is 09:30 in Istanbul. The block must say 09:30, which is the
    # whole point: the user reported being told 08:00 at noon.
    block = now_block(datetime(2026, 8, 25, 6, 30, tzinfo=timezone.utc))
    assert "2026-08-25 09:30" in block
    assert "+03:00, Europe/Istanbul" in block
    assert "06:30" not in block


def test_the_block_names_the_day_in_both_languages():
    block = now_block(datetime(2026, 8, 25, 12, 0, tzinfo=TZ))
    assert "Tuesday" in block  # 25 August 2026 is a Tuesday
    assert "Salı" in block
    assert "25 Ağustos 2026" in block


def test_a_naive_or_foreign_instant_is_converted_not_relabelled():
    """23:00 in London is 01:00 the *next day* in Istanbul."""
    london = ZoneInfo("Europe/London")
    block = now_block(datetime(2026, 8, 25, 23, 0, tzinfo=london))
    assert "2026-08-26 01:00" in block
    assert "Wednesday" in block
    assert "Çarşamba" in block


def test_midnight_does_not_roll_the_date_backwards():
    block = now_block(datetime(2026, 1, 1, 0, 0, tzinfo=TZ))
    assert "2026-01-01 00:00" in block
    assert "1 Ocak 2026" in block


def test_every_day_and_month_name_is_reachable_and_aligned():
    """A day name off by one would make every relative date wrong by a day."""
    assert len(TR_DAYS) == len(EN_DAYS) == 7
    assert len(TR_MONTHS) == 12
    # 2026-08-24 is a Monday, so seven consecutive days cover the whole week in
    # order and pin the indexing to a real calendar rather than to itself.
    for offset, (english, turkish) in enumerate(zip(EN_DAYS, TR_DAYS)):
        instant = datetime(2026, 8, 24 + offset, 9, 0, tzinfo=TZ)
        block = now_block(instant)
        assert english in block, english
        assert turkish in block, turkish


def test_the_block_forbids_writing_times_in_utc():
    """The instruction, not just the data. Pinned because it is the fix."""
    block = now_block()
    assert "Turkey time (UTC+03:00)" in block
    assert "Never convert a time to UTC" in block
    assert "bugün" in block and "yarın" in block


def test_the_block_is_the_same_length_whatever_the_hour():
    """Its token cost is charged against every turn's context window.

    `agents/main/agent.py::main_compaction` measures `system_prompt()` to size
    the compaction threshold. A block whose length depended on the hour would
    move that threshold during a conversation, which is the disagreement between
    "70% full" and the point where compaction fires that the function's own
    docstring warns about. Only the date and the month name vary.
    """
    day = datetime(2026, 8, 25, tzinfo=TZ)
    lengths = {len(now_block(day.replace(hour=h, minute=m)))
               for h in range(24) for m in (0, 7, 59)}
    assert len(lengths) == 1


def test_live_results_carry_a_turkish_timestamp():
    import json

    from agents.shared.results import live_result

    envelope = json.loads(live_result("vakif", "exchange_rates", lambda: {"USD": 1}))
    assert envelope["retrieved_at"].endswith("+03:00")
    # Parseable as well as correct: the offset is real, not decoration.
    parsed = datetime.fromisoformat(envelope["retrieved_at"])
    assert parsed.utcoffset().total_seconds() == 3 * 3600


def test_a_failed_live_call_is_still_stamped_in_turkish_time():
    """The envelope's stamp is built before the call, so a refusal keeps it."""
    import json

    from agents.shared.results import live_result

    def refused():
        raise ValueError("This bank does not publish that calculator.")

    envelope = json.loads(live_result("adil", "finance_quote", refused))
    assert envelope["status"] == "unavailable"
    assert envelope["retrieved_at"].endswith("+03:00")


def test_storage_timestamps_stay_utc():
    """`stamp_tr` is for agents. Status files and build reports must not move."""
    from banks import clock

    instant = datetime(2026, 8, 9, 13, 51, tzinfo=timezone.utc)
    assert clock.stamp(instant) == "2026-08-09T13:51:00+00:00"
    assert clock.stamp_tr(instant) == "2026-08-09T16:51:00+03:00"
    assert clock.display(instant) == "2026-08-09 16:51 +03"


@pytest.mark.parametrize(
    "builder",
    [
        "agents.main.agent:system_prompt",
        "agents.recommendation.agent:system_prompt",
    ],
)
def test_the_agents_that_answer_the_user_are_told_the_time(builder):
    import importlib

    module_name, attribute = builder.split(":")
    prompt = getattr(importlib.import_module(module_name), attribute)()
    assert "CURRENT DATE AND TIME" in prompt
    assert "Europe/Istanbul" in prompt


def test_the_clock_is_read_at_call_time_not_at_import():
    """A prompt built once per process would state the date the server booted."""
    from agents.main.agent import system_prompt

    first = system_prompt()
    assert datetime.now(TZ).strftime("%Y-%m-%d") in first


def test_every_bank_specialist_is_told_the_time(monkeypatch):
    """Ten specialists, one prompt assembly -- so one assertion covers all ten.

    A specialist is the agent that reads a campaign's end date off a page and
    decides whether it has passed, and it sees no conversation history to infer
    the date from: one delegated instruction and its own tool results.
    """
    from agents.shared import specialists

    captured = {}
    monkeypatch.setattr(specialists, "get_llm", lambda *a, **k: object())
    monkeypatch.setattr(specialists, "get_checkpointer", lambda: None)
    monkeypatch.setattr(specialists, "resolve_model_key", lambda key: "gemma")
    monkeypatch.setattr(specialists, "usable_context_window", lambda *a, **k: 100_000)
    monkeypatch.setattr(
        specialists, "build_compaction", lambda window, specialist: "COMPACTION"
    )
    monkeypatch.setattr(
        specialists, "create_agent", lambda **kwargs: captured.update(kwargs)
    )

    specialists.build_specialist("vakif")
    prompt = captured["system_prompt"]
    assert "CURRENT DATE AND TIME" in prompt
    assert datetime.now(TZ).strftime("%Y-%m-%d") in prompt
    # Last, so the clock is the most recent thing the model read.
    assert prompt.rstrip().endswith("not evidence about today.")


def test_the_drafting_agent_is_told_the_time(monkeypatch):
    """The one agent whose entire job is turning time words into integers."""
    from agents.automation import agent as drafting

    captured = {}
    monkeypatch.setattr(drafting, "get_llm", lambda *a, **k: object())
    monkeypatch.setattr(
        drafting, "create_agent", lambda **kwargs: captured.update(kwargs)
    )

    drafting.build_automation_agent()
    assert "CURRENT DATE AND TIME" in captured["system_prompt"]
    assert datetime.now(TZ).strftime("%Y-%m-%d") in captured["system_prompt"]
