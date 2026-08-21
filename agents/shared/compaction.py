"""Summarising a thread before it fills its window.

Every agent here is compacted the same way -- the supervisor and each of the ten
bank specialists -- because each is a separate instance with its own thread and
its own history. A specialist's thread grows with bank JSON and nobody is
watching it; left alone it hits the window mid-delegation and the turn fails.

This subclasses LangChain's `SummarizationMiddleware` rather than replacing it:
the parts worth keeping are the ones that are easy to get wrong -- deciding
where to cut without separating an AI tool call from its ToolMessage, and
counting messages against a threshold. The parts overridden below are the ones
whose defaults are wrong for an agent that must not lose what it was told.

Three defaults are replaced, and each of them silently destroys history:

    trim_tokens_to_summarize=4000   the summariser is shown only the last 4000
                                    tokens of the thread. Compacting a 90k-token
                                    conversation, it would read about 4% of it and
                                    describe the rest from nothing. Set to None
                                    here: the summariser reads everything.

    _create_summary swallowing      upstream catches every exception and returns
                                    "Error generating summary: ..." *as the
                                    summary*. The middleware has already emitted
                                    REMOVE_ALL_MESSAGES by then, so one failed
                                    call replaces the whole conversation with an
                                    error string. Here it propagates: a failed
                                    compaction leaves the thread untouched and is
                                    retried on the next turn.

    an English wrapper              upstream injects "Here is a summary of the
                                    conversation to date:". A Turkish thread would
                                    resume with an English framing around an
                                    English summary, and the model follows the
                                    language it can see. Replaced with a
                                    language-neutral tag, and the summary itself
                                    is written in the conversation's language.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from langchain.agents.middleware import SummarizationMiddleware
from langchain_core.messages import AnyMessage, HumanMessage
from langchain_core.messages.utils import get_buffer_string

from config.settings import settings
from llm import get_llm
from llm.context import fixed_overhead, reported_context_window
from llm.factory import resolve_model_key

logger = logging.getLogger(__name__)


# Share of the summariser's window given to the thread it is reading. The rest
# is left for the summary it writes: a model asked to read right up to its window
# has nowhere to put the answer, and comes back empty or truncated by the server.
_SUMMARY_INPUT_SHARE = 0.75


class SummaryUnavailable(RuntimeError):
    """The summary could not be produced, so the thread was left alone.

    Raised instead of returning something usable-looking. Compaction failing
    costs one oversized request; compaction "succeeding" with an error string or
    an empty summary costs the entire conversation.
    """


# Written in the conversation's language, on purpose.
#
# Measured: LangChain's default prompt turned a Turkish exchange about konut
# finansmanı into English prose with English section headings. After one
# compaction the model's whole visible history would be English, and it answers
# in the language it can see -- so a Turkish user starts getting English back.
#
# The sections are described rather than dictated as literal headings, so the
# model writes them in the same language as everything else.
SUMMARY_PROMPT = """You are compacting a conversation so it can continue \
within a smaller context window.

Write a summary of the conversation below. The summary replaces the messages \
it covers: after this, the conversation continues from your summary alone, so \
anything you leave out is gone.

Write the summary in the same language the conversation is in. If the \
conversation is in Turkish, write Turkish. If it is in English, write English. \
Match the conversation, not these instructions. This includes any headings you \
use.

Cover, each under its own heading:

- What the user is trying to do, in their own terms.
- Every concrete figure that was established: amounts, terms in months, \
currencies, profit rates, instalments, and which bank each belongs to. Copy \
these exactly; a rounded or half-remembered rate is worse than no rate.
- Which banks were consulted and what each one answered, including any that \
could not answer and why.
- What was decided, what was ruled out, and the reason.
- What the user asked for most recently that has not been answered yet.

Do not add anything that is not in the conversation. If a section has nothing \
in it, say so in one short line rather than inventing content.

Respond with the summary only.

<conversation>
{messages}
</conversation>"""


class ThreadCompaction(SummarizationMiddleware):
    """Compaction that reads the whole thread and never invents a summary."""

    #: Tokens of thread the summariser may be shown in one call. Set by
    #: `build_compaction` from the summarising model's own window. Above this the
    #: thread is folded in passes rather than shortened.
    summary_input_budget: int = 0

    def _create_summary(self, messages_to_summarize: list[AnyMessage]) -> str:
        """Summarise, letting failure be failure.

        Deliberately does not call `_trim_messages_for_summary`. With
        `trim_tokens_to_summarize=None` that method is a pass-through, but it
        wraps its body in `except Exception: return messages[-15:]` -- a silent
        tail slice that would be indistinguishable from a real summary of a long
        thread. Nothing here shortens the input.
        """
        if not messages_to_summarize:
            raise SummaryUnavailable("Nothing to summarise.")

        groups = self._fit_to_budget(messages_to_summarize)
        summaries = [self._summarise_once(group) for group in groups]
        if len(summaries) == 1:
            summary = summaries[0]
        else:
            # Fold the passes together. Every token of the thread has been read
            # by the model on the way here; this summarises summaries, which is
            # lossy in the way summarising always is, and never silent.
            logger.info("Thread exceeded one pass; folding %d summaries", len(summaries))
            summary = self._summarise_once(
                [HumanMessage(part) for part in summaries]
            )

        logger.info(
            "Compacted %d messages into a %d-character summary",
            len(messages_to_summarize),
            len(summary),
        )
        return summary

    def _summarise_once(self, messages: list[AnyMessage]) -> str:
        """One summarising call. Raises rather than returning something usable."""
        # XML, matching upstream: URL-based multimodal blocks stay visible to the
        # summariser while raw message metadata stays out of its token budget.
        conversation = get_buffer_string(messages, format="xml")
        response = self.model.invoke(
            self.summary_prompt.format(messages=conversation),
            config={"metadata": {"lc_source": "summarization"}},
        )
        summary = response.text.strip()
        if not summary:
            raise SummaryUnavailable(
                "The summariser returned nothing. Keeping the thread as it is."
            )
        return summary

    def _fit_to_budget(self, messages: list[AnyMessage]) -> list[list[AnyMessage]]:
        """Split the thread into consecutive groups the summariser can read.

        Normally returns one group: compaction triggers below the window, so
        everything fits. It does not fit when a thread arrives from outside the
        trigger -- a conversation re-seeded from the database, where the stored
        history can be longer than the window it is being loaded into.

        Splitting happens on message boundaries only. A message is never cut,
        and nothing is dropped: every group is summarised and the summaries are
        folded together, so all of it reaches the model eventually.
        """
        budget = self.summary_input_budget
        if budget <= 0 or self.token_counter(messages) <= budget:
            return [messages]

        groups: list[list[AnyMessage]] = []
        current: list[AnyMessage] = []
        for message in messages:
            alone = self.token_counter([message])
            if alone > budget:
                raise SummaryUnavailable(
                    f"A single message needs {alone} tokens and the summariser "
                    f"can read {budget}. It cannot be summarised without cutting "
                    "it, which would lose content silently."
                )
            if current and self.token_counter([*current, message]) > budget:
                groups.append(current)
                current = [message]
            else:
                current.append(message)
        if current:
            groups.append(current)
        return groups

    #: Set only for the duration of `compact_now`. Safe as instance state
    #: because an agent -- and so its middleware -- is built per request.
    _forced: bool = False

    def _should_summarize(self, messages, total_tokens) -> bool:
        """The threshold, unless this compaction was asked for by hand."""
        if self._forced:
            return True
        return super()._should_summarize(messages, total_tokens)

    def compact_now(self, state) -> dict | None:
        """Compact this thread regardless of how full it is.

        Runs the same path as the automatic case -- same cutoff, same partition,
        same summary, same rebuild -- with only the threshold check bypassed, so
        a hand-triggered compaction cannot drift from the one that fires on its
        own. Returns the state update to apply, or None when there is nothing
        ahead of the preserved tail to summarise.
        """
        self._forced = True
        try:
            return self.before_model(state, None)
        finally:
            self._forced = False

    @staticmethod
    def _build_new_messages(summary: str) -> list[HumanMessage]:
        """Frame the summary without choosing a language for it.

        A tag rather than a sentence. Upstream's "Here is a summary of the
        conversation to date:" is English, and English framing around a Turkish
        summary is exactly the mixed signal that makes a model switch language.
        Markup carries the same meaning in every language.
        """
        return [
            HumanMessage(
                content=f"<conversation_summary>\n{summary}\n</conversation_summary>",
                additional_kwargs={"lc_source": "summarization"},
            )
        ]


def build_compaction(usable_window: int, *, specialist: bool) -> ThreadCompaction:
    """One compaction middleware, configured for its tier.

    Args:
        usable_window: what the conversation can occupy -- the window the server
            reports, minus the system prompt and tool schemas that every request
            carries. The fraction is taken of this, so "70%" means 70% of the
            space actually available rather than 70% of a number that includes
            room the conversation can never use.
        specialist: read the specialist's own threshold and tail length. The two
            tiers are tuned apart: a bank thread fills at a different rate than a
            conversation, and only the supervisor's is ever shown to anyone.
    """
    fraction = (
        settings.COMPACT_SPECIALIST_AT_FRACTION
        if specialist
        else settings.COMPACT_AT_FRACTION
    )
    keep = (
        settings.COMPACT_SPECIALIST_KEEP_MESSAGES
        if specialist
        else settings.COMPACT_KEEP_MESSAGES
    )
    # What the summariser itself can read in one call: its own window, less the
    # instructions wrapped around the thread, less room to write the summary
    # back. A thread longer than this is folded in passes -- see _fit_to_budget.
    # Nothing here shortens the thread; this only decides how many calls it takes.
    summariser = resolve_model_key(settings.COMPACT_MODEL)
    summariser_window = reported_context_window(summariser)
    prompt_cost = fixed_overhead(SUMMARY_PROMPT)
    budget = int((summariser_window - prompt_cost) * _SUMMARY_INPUT_SHARE)

    middleware = ThreadCompaction(
        model=get_llm(settings.COMPACT_MODEL),
        # Absolute, not ("fraction", x). The fraction form resolves against the
        # model's declared profile, which is the *reported* window and knows
        # nothing about the tool schemas riding on every call. Computing the
        # threshold here keeps one definition of "70% full" shared with anything
        # that displays it.
        trigger=("tokens", int(usable_window * fraction)),
        keep=("messages", keep),
        summary_prompt=SUMMARY_PROMPT,
        # No truncation. See the module docstring: the default shows the
        # summariser the last 4000 tokens and discards the rest of the thread.
        trim_tokens_to_summarize=None,
    )
    middleware.summary_input_budget = budget
    return middleware


@dataclass(frozen=True)
class ContextLevel:
    """How full one thread is, in the same units its own threshold uses.

    Counted with the middleware's own `token_counter` against the same usable
    window its trigger was computed from, so the number shown to a user and the
    number that fires compaction can never disagree.
    """

    used_tokens: int
    usable_tokens: int
    compact_at_tokens: int
    keep_messages: int
    message_count: int

    @property
    def fraction(self) -> float:
        """0.0 to 1.0. Clamped at the top: a thread can exceed its usable window
        between the call that filled it and the compaction that follows, and a
        ring drawn past full reads as broken rather than as urgent."""
        if self.usable_tokens <= 0:
            return 0.0
        return min(self.used_tokens / self.usable_tokens, 1.0)

    @property
    def tokens_until_compaction(self) -> int:
        """What is left before compaction happens on its own. Never negative."""
        return max(self.compact_at_tokens - self.used_tokens, 0)


def measure(middleware: ThreadCompaction, messages: list, usable_window: int) -> ContextLevel:
    """Read one thread's level with the middleware that governs it."""
    [clause] = middleware._trigger_clauses  # noqa: SLF001 - one definition of the threshold
    return ContextLevel(
        used_tokens=middleware.token_counter(messages),
        usable_tokens=usable_window,
        compact_at_tokens=int(clause["tokens"]),
        keep_messages=int(middleware.keep[1]),
        message_count=len(messages),
    )
