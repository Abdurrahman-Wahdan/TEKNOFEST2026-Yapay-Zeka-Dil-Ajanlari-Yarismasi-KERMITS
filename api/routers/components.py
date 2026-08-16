"""The components a topic page renders, produced from the RAG corpus.

This is the seam between the agent that reads the corpus and the UI that draws
it. The split it sits on:

    live bank endpoints -> deterministic software (banks.py, compare.py)
    the RAG corpus      -> components produced by a model, served from here

Software cannot compare corpus content: `corpus.models.Document` carries no
rate, term, amount or product-type field, only free text. A model can read that
text and lay it out; nothing else can. So every table on a topic page arrives
through this router, and no live figure ever does.

Until the producer lands, these serve hand-written fixtures from
`api/fixtures/components/`. The route signature and the response shape do not
change when it does -- that is the whole point of building the UI against them
now. `source` says which you are looking at, so placeholder content is never
mistaken for bank data.
"""

import json
import logging
from pathlib import Path as FilePath

from fastapi import APIRouter, HTTPException, Path, status

from ..schemas.components import CategoryOut, ComponentsResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/components", tags=["components"])

FIXTURES = FilePath(__file__).resolve().parent.parent / "fixtures" / "components"

# The topic pages, in nav order. Keyed by route segment, so /finansman asks for
# "finansman" and nothing has to translate between the two.
#
# A category with no fixture yet is still a valid category: it answers with an
# empty component list, and the page says so. Removing it from here would 404 a
# route that exists, which is a worse lie than "nothing here yet".
CATEGORIES: dict[str, str] = {
    "finansman": "Finansman",
    "kartlar": "Kartlar",
    "kampanyalar": "Kampanyalar",
    "doviz-altin": "Döviz & Altın",
    "yatirim": "Yatırım & Birikim",
    "sigorta": "Sigorta & Emeklilik",
    "ucretler": "Ücretler & Komisyonlar",
    "dijital": "Dijital Bankacılık",
    "subeler": "Şube & ATM",
}


def _fixture_path(category: str) -> FilePath:
    """Resolve a category to its fixture, refusing anything outside the dir.

    `category` is already constrained to CATEGORIES before this is called, so
    the containment check is belt-and-braces -- but it is one line, and the day
    somebody makes categories dynamic it is the line that stops `../../.env`
    being served as a component list.
    """
    path = (FIXTURES / f"{category}.json").resolve()
    if not path.is_relative_to(FIXTURES.resolve()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bad category.")
    return path


@router.get("", response_model=list[CategoryOut])
def all_categories() -> list[CategoryOut]:
    """Every topic page, and whether a producer has filled it yet."""
    return [
        CategoryOut(
            key=key,
            label=label,
            has_components=_fixture_path(key).exists(),
        )
        for key, label in CATEGORIES.items()
    ]


@router.get("/{category}", response_model=ComponentsResponse)
def category_components(
    category: str = Path(description="A key from GET /api/components."),
) -> ComponentsResponse:
    """The ordered components for one topic page.

    An unknown category 404s naming the valid ones. A known category with no
    fixture answers 200 with an empty list: "this page has no content yet" is
    an answer, not a failure, and the UI has a state for it.
    """
    if category not in CATEGORIES:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Unknown category {category!r}. Valid: {', '.join(CATEGORIES)}.",
        )

    path = _fixture_path(category)
    if not path.exists():
        return ComponentsResponse(category=category, components=[])

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # A malformed fixture is our bug, not the caller's. Log it loudly and
        # answer empty rather than 500 -- the page still renders its comparator
        # and says the content is missing.
        logger.exception("Could not read component fixture %s", path)
        return ComponentsResponse(category=category, components=[])

    return ComponentsResponse(
        category=category,
        generated_at=payload.get("generated_at", ""),
        source=payload.get("source", "fixture"),
        components=payload.get("components", []),
    )
