"""Embed the clean corpus into Qdrant and search it.

    from index import run, search

    run()                                  # nightly sync of documents.jsonl -> Qdrant
    hits = search("konut finansmanı kâr payı oranı")
"""

from .chunk import chunks, linked_from_map
from .models import Chunk, RetrievedChunk
from .report import IndexReport
from .retrieve import search
from .sync import run

__all__ = [
    "Chunk",
    "IndexReport",
    "RetrievedChunk",
    "chunks",
    "linked_from_map",
    "run",
    "search",
]
