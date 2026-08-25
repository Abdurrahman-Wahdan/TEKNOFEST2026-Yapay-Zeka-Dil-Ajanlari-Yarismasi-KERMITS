"""The supervisor's one non-bank tool: a directory of this site's comparison tables.

TF26 already publishes 403 comparison tables at `/tr/urunler` and
`/tr/kampanyalar` -- one per product or campaign topic, every participation bank
side by side. Before this tool the assistant could not see them, so it would
answer a question the site already answers on a page, and never mention that the
page exists. The conversation and the site read as two products.

**This is a directory, not a source.** A hit says "a table on this topic exists,
and here is its address". It carries no rate, no condition and no bank fact --
those come from the bank specialists, as they did before, and nothing here
changes that rule. The tool's own output repeats this, because a model shown a
`docstring` describing what a table compares will otherwise be tempted to answer
from it.

The address comes from the `ui_url` payload field, stamped onto every point by
`dataprep/stamp_table_urls.py`. It is site-relative on purpose
(`/tr/kampanyalar?tablo=...`): `UI/src/components/chat/AgentMarkdown.tsx` opens
`http` links in a new tab and relative ones in place, so a relative link takes
the reader to the table without throwing away the conversation.
"""

import logging

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from corpus.tables import search_tables

logger = logging.getLogger(__name__)

RESULTS_PER_CALL = 5

# Below this similarity there is no table on the topic, so nothing is returned.
#
# Measured, not chosen: 30 tables sampled from the pool of 403 were queried by
# their own topic, and six deliberately off-topic queries ("kedi maması
# fiyatları", "futbol maç sonuçları", ...) were run against the same collection.
# The intended table came back in the top 5 every time, 29 of 30 at rank 1. The
# two populations separate cleanly and do not overlap:
#
#     the intended table   min 0.538   median 0.838   max 0.920
#     off-topic hits       min 0.289   median 0.356   max 0.466
#
# 0.50 sits in the gap. At that value the sweep keeps 100% of intended tables and
# drops 100% of off-topic hits; so does 0.48, and 0.55 starts losing real ones.
#
# Without a floor the tool always returns five rows, because a vector search
# always has a top five -- "kedi maması fiyatları" came back with five credit-card
# tables at 0.33-0.38. A model shown a numbered list will offer something from it.
# The sample is 30 tables and 6 off-topic queries; the gap is wide, but re-measure
# before moving this rather than nudging it.
MIN_SCORE = 0.50


class FindTableInput(BaseModel):
    query: str = Field(
        description=(
            "The topic to look for, in the user's own words -- a product or "
            "campaign subject such as 'konut finansmanı' or 'araç bakım "
            "indirimi'. Matching is by meaning, not keyword, so a short "
            "descriptive phrase works better than one noun."
        )
    )
    intent: str = Field(
        default="",
        description=(
            "One sentence on what you are trying to find. This is fed to the "
            "embedding as a retrieval instruction and measurably changes which "
            "tables come back, so it is not a formality."
        )
    )


def _format(hits: list[dict]) -> str:
    if not hits:
        return ("Bu konuda sitede bir karşılaştırma tablosu yok. Cevabını normal "
                "şekilde ver, uydurma bir sayfa adresi VERME.")
    lines = []
    for i, hit in enumerate(hits, 1):
        where = "/".join(x for x in (hit["category"], hit["subcategory"]) if x)
        lines.append(
            f"{i}. {hit['topic']}"
            + (f"  [{where}]" if where else "")
            + f"  benzerlik={hit['score']:.2f}\n"
            f"   Ne karşılaştırıyor: {hit['docstring']}\n"
            # The finished markdown, not the bare address, because assembling it
            # is where the model goes wrong. Measured on 2026-08-25: given
            # `/tr/urunler?tablo=altın-katılma-hesabı` it wrote
            # `https://www.kermits.com.tr/tr/urunler?tablo=...`, inventing a host
            # that exists nowhere in this repository. A line to copy verbatim
            # leaves nothing to reconstruct. The backend and the UI both repair
            # such a link anyway -- this is about not needing them to.
            #
            # A table with no address is still a real answer to "does one exist",
            # so it is listed, but with no link line at all: the only thing a
            # model can do with a blank address is fill it in.
            + (f"   Kullanıcıya verilecek link (AYNEN kopyala): "
               f"[{hit['topic']}]({hit['ui_url']})" if hit["ui_url"]
               else "   (Bu tablonun sayfa adresi kayıtlı değil -- link VERME.)")
        )
    return "\n".join(lines) + (
        "\n\nBunlar TABLO KÜNYELERİDİR, banka bilgisi değil: içlerinde oran, ücret "
        "ya da koşul yok. Rakamları yine banka uzmanlarından al. Kullanıcının "
        "sorduğu konuya gerçekten karşılık gelen TEK tabloyu öner ve yukarıdaki "
        "markdown linki AYNEN kopyala: başına alan adı EKLEME, adresi yeniden "
        "yazma, kısaltma."
    )


def build_table_directory_tool() -> StructuredTool:
    def find_comparison_table(query: str, intent: str = "") -> str:
        found = search_tables(query, intent=intent, limit=RESULTS_PER_CALL)
        hits = [h for h in found if h["score"] >= MIN_SCORE]
        logger.info("table_directory query=%r hits=%d/%d above %.2f",
                    query, len(hits), len(found), MIN_SCORE)
        return _format(hits)

    return StructuredTool.from_function(
        func=find_comparison_table,
        name="find_comparison_table",
        description=(
            "Check whether this site already publishes a comparison table on the "
            "user's topic, and get its page address so you can link the user to "
            "it. Returns up to 5 table titles with what each one compares and its "
            "address on this site. It is a page directory: it contains NO rates, "
            "fees, conditions or bank facts, so it never replaces a bank "
            "specialist call and is never a citation for a factual claim. Use it "
            "in addition to the specialists when the question is about a product "
            "or campaign topic that a table would cover."
        ),
        args_schema=FindTableInput,
    )
