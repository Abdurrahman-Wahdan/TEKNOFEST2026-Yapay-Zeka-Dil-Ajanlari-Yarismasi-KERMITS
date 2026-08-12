"""What goes into Qdrant, and what comes back out.

A `Chunk` is one retrievable unit — a web page's section, a PDF's page, or a
whole campaign — carrying the text to embed, the text to show, and the metadata
the agent needs when it cites the answer. `RetrievedChunk` is a chunk plus the
score a search gave it.

Frozen dataclasses, like the rest of the project. The corpus already did the
hard part: every unit arrives with its own `text_hash` (the change key) and
`cite_url` (the openable link), so this layer only has to shape them for the
store.
"""

from dataclasses import dataclass, field

# The unit a chunk came from. `document` is the whole-document campaign case.
UNIT_KINDS = ("section", "page", "document")


@dataclass(frozen=True)
class Chunk:
    """One unit to embed and index."""

    # ----- identity -----
    chunk_id: str          # "{doc_id}:{unit_kind}:{unit_index}" (+ "#n" if split)
    doc_id: str
    text_hash: str         # the unit's hash -- the sync key; unchanged => not re-embedded

    # ----- what to embed vs what to show -----
    # `embed_text` carries a context header (bank, title, heading) so a mid-document
    # chunk still knows its subject. `text` is the clean body the agent displays.
    embed_text: str
    text: str

    # ----- the link (the priority metadata) -----
    cite_url: str          # ".../page#anchor" or ".../form.pdf#page=7"
    url: str

    # ----- everything else the payload holds -----
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievedChunk:
    """A chunk a search returned, with its score."""

    score: float
    cite_url: str
    text: str
    payload: dict = field(default_factory=dict)

    @property
    def bank(self) -> str:
        return self.payload.get("bank", "")

    @property
    def from_vision(self) -> bool:
        """Whether this text was read by OCR -- the agent should hedge if so."""
        return bool(self.payload.get("from_vision"))
