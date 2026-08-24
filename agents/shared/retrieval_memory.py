"""What a specialist decided to keep from its own retrieval, and the middleware
that acts on it.

A specialist that searches its bank's corpus pulls whole chunks into its thread
-- measured across the collection, a mean of 2400 characters each and up to
9000, five at a time. Most of what a search returns is not what the specialist
was looking for, and without a way to put it back down, three searches leave the
thread carrying twenty passages to answer one question.

So the search tools let the model name the results it is done with
(`not_useful`) and the ones to protect (`useful`). Those decisions land in a
`RetrievalMemory`, and this middleware applies them to the thread before the
next model call.

Two properties are deliberate:

**It only ever touches retrieval output.** Eligibility is decided by which tool
produced a message -- `corpus.search.PRUNABLE_TOOLS` -- not by what the message
contains. A `finance_quote` envelope, a user turn, or a compaction summary
cannot be edited through this path even if it happens to contain the text
`point_id=`. "The model can drop things it does not need" has to mean retrieval
results and nothing else; a live quote it decided it disliked is not its to
delete.

**The trim is written through, not re-applied.** `before_model` returns the
edited messages under their existing ids, and `add_messages` replaces a same-id
message rather than appending it -- so the checkpointer stores the shortened
version and the discarded passage is gone for good. The alternative, filtering
the model's view on every call, would leave the thread growing underneath and
would forget the decision the moment the specialist was rebuilt for the next
turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from langchain.agents.middleware import AgentMiddleware

from corpus.search import prune_messages


@dataclass
class RetrievalMemory:
    """One specialist's retrieval decisions, shared by its tools and middleware.

    Built per delegated turn, alongside the specialist itself, so one bank's
    decisions can never reach another's thread.
    """

    marked: set[str] = field(default_factory=set)
    """point_ids the model asked to keep. Protected from the physical trimming
    that happens when a thread fills, so "this is the passage the answer rests
    on" survives a compaction that drops its neighbours."""

    discarded: set[str] = field(default_factory=set)
    """point_ids the model is done with. Removed from the thread on the next
    model call, without waiting for the context to fill."""


class RetrievalPruning(AgentMiddleware):
    """Apply a specialist's `not_useful` decisions to its own thread."""

    def __init__(self, memory: RetrievalMemory) -> None:
        self.memory = memory
        self.tools = []

    def before_model(self, state, runtime) -> dict | None:  # noqa: ARG002
        pruned = prune_messages(state["messages"], self.memory.discarded)
        # None rather than an empty update: nothing was decided, so nothing is
        # written, and the graph does not record a checkpoint that changed nothing.
        return {"messages": pruned} if pruned else None
