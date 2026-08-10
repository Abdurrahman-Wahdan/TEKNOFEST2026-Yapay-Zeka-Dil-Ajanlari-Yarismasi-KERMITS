"""Building the strings that get embedded — and the model quirk that lives here.

Two texts are built differently: a *passage* (an indexed chunk) and a *query*
(what the agent asks). Qwen3-Embedding wants them asymmetric — a passage is
embedded as plain text, a query is wrapped in an instruction — and getting that
wrong quietly halves recall. That asymmetry is confined to this module so
nothing else has to know which model is in use; swapping the model changes only
the rules here.

    from index.embed_text import passage_text, query_text

    passage_text("Kuveyt Türk — Kâr Payı Oranları", "…the chunk body…")
    query_text("Kuveyt Türk konut finansmanı kâr payı oranı nedir?")
"""

from config.settings import settings

# What the retriever is for, told to the query encoder. Qwen3-Embedding conditions
# the query vector on this; passages are never given it. Turkish, because the
# corpus and the questions are Turkish.
TASK = ("Bir kullanıcının Türk katılım bankalarının kampanya, ürün, ücret ve "
        "kâr payı oranları hakkındaki sorusuna yanıt olabilecek metni getir.")

# Model families and how each wants its text. Qwen3 is the configured model;
# the others are here so a future switch is a one-line change, not a hunt.
#   qwen3  : passage plain,           query "Instruct: {task}\nQuery: {q}"
#   e5     : passage "passage: {t}",  query "query: {q}"
#   plain  : no affixes either side   (bge-m3, gte, Trendyol)
_E5_MARKERS = ("e5",)
_QWEN_MARKERS = ("qwen",)


def _family(model: str) -> str:
    name = (model or "").lower()
    if any(m in name for m in _QWEN_MARKERS):
        return "qwen3"
    if any(m in name for m in _E5_MARKERS):
        return "e5"
    return "plain"


def passage_text(header: str, body: str, model: str | None = None) -> str:
    """The text to embed for one chunk.

    `header` is the context line (bank — title — heading) that lets a chunk from
    the middle of a document still carry its subject; `body` is the clean text.
    """
    combined = f"{header}\n\n{body}".strip() if header else body.strip()
    if _family(model or settings.EMBEDDING_MODEL) == "e5":
        return f"passage: {combined}"
    return combined


def query_text(query: str, model: str | None = None) -> str:
    """The text to embed for a search query."""
    query = query.strip()
    family = _family(model or settings.EMBEDDING_MODEL)
    if family == "qwen3":
        return f"Instruct: {TASK}\nQuery: {query}"
    if family == "e5":
        return f"query: {query}"
    return query


def header_for(bank: str, title: str, detail: str = "") -> str:
    """The context line prepended to a chunk before embedding.

    Detail is the heading path (sections) or "sayfa N" (pages); empty for a
    whole-campaign chunk. Empty parts are dropped so the line never has a
    dangling separator.
    """
    parts = [p for p in (bank, title, detail) if p]
    return " — ".join(parts)
