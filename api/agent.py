"""The seam where the LangGraph agent plugs in.

**Status: this is a retrieval-and-answer pipeline, not the agent.** The agent
layer -- the one that decides between calling a bank endpoint and searching the
corpus, per HANDOFF §2 -- is not built yet. What is here retrieves from the
index and streams an answer from the chat model, which is enough for the
dashboard's chat panel to be real while that work happens.

Replacing it means changing `answer()` and nothing else. The router knows only
that it yields `StreamEvent`s, so a LangGraph `astream_events` loop can be
dropped in without touching HTTP, persistence, or the frontend.

Two rules it already honours, because they are not the agent's to break:

- **Every claim carries its citation.** Retrieved chunks are streamed to the
  client as they are chosen, so the sources appear beside the answer rather
  than being reconstructed afterwards.
- **Expired campaigns are filtered at query time**, not remembered. The index
  is not re-embedded when a campaign expires; `active_only` is what makes the
  same index correct tomorrow.
"""

import logging
from typing import Iterator

from config.settings import settings
from index.retrieve import search
from llm import get_llm

from .converters import chunk_out
from .schemas.chat import StreamEvent

logger = logging.getLogger(__name__)

# The model is told to answer only from what it was given. It is not told to be
# helpful when it has nothing -- a bank-facing tool that fills a gap with a
# plausible rate is worse than one that says it does not know.
SYSTEM_PROMPT = """\
Sen Türk katılım bankalarının kampanyalarını ve ürünlerini karşılaştıran bir \
asistansın.

Kurallar:
- Yalnızca sana verilen kaynaklardaki bilgiyi kullan. Kaynaklarda yoksa \
"bu bilgi kaynaklarımda yok" de.
- Hiçbir oranı, taksiti veya tutarı kendin hesaplama. Sayı bankanın verdiği \
sayıdır.
- Her iddiadan sonra kaynağı [1], [2] biçiminde numarayla belirt.
- Kullanıcı hangi dilde sorduysa o dilde cevap ver.
"""


def _sources_block(chunks) -> str:
    """The retrieved passages, numbered so the model's [n] markers line up."""
    parts = []
    for number, chunk in enumerate(chunks, start=1):
        bank = chunk.payload.get("bank", "?")
        title = chunk.payload.get("title", "")
        # A passage read by OCR is labelled in the prompt itself, so the model
        # hedges on a figure taken from a scan rather than stating it flatly.
        scanned = " (taranmış sayfa, rakamlar için dikkat)" if chunk.from_vision else ""
        parts.append(f"[{number}] {bank} — {title}{scanned}\n{chunk.text}")
    return "\n\n".join(parts)


def answer(question: str, history: list[tuple[str, str]] | None = None
           ) -> Iterator[StreamEvent]:
    """Answer a question, yielding stream events as the work happens.

    Args:
        question: the user's question, in Turkish or English.
        history: (role, content) turns, oldest first, already windowed by the
            caller to API_CHAT_HISTORY_TURNS.

    Yields:
        StreamEvent: status, then citations, then tokens. The router turns these
        into SSE frames and persists the assembled answer.
    """
    yield StreamEvent(type="status", stage="retrieving")

    try:
        chunks = search(question, k=settings.INDEX_RETRIEVE_TOP_K)
    except Exception:
        logger.exception("Retrieval failed")
        yield StreamEvent(
            type="error", detail="The search index is unavailable."
        )
        return

    for chunk in chunks:
        yield StreamEvent(type="citation", citation=chunk_out(chunk))

    yield StreamEvent(type="status", stage="writing")

    messages = [("system", SYSTEM_PROMPT)]
    messages.extend(history or [])
    messages.append(
        ("human", f"Kaynaklar:\n\n{_sources_block(chunks)}\n\nSoru: {question}")
    )

    try:
        for piece in get_llm("chat").stream(messages):
            text = piece.content
            if text:
                yield StreamEvent(type="token", text=text)
    except Exception:
        logger.exception("Generation failed")
        yield StreamEvent(type="error", detail="The language model is unavailable.")
