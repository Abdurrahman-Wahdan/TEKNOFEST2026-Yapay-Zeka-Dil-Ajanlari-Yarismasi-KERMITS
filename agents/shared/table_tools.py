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
# Candidates fetched per RESULTS_PER_CALL before the publishable filter runs.
OVERFETCH = 3


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
        "\n\nBunlar anlamsal yakınlığa göre sıralanmış ADAY TABLO KÜNYELERİDİR. "
        "Benzerlik puanı bir eleme kuralı değildir ve aday gösterilmesi tek "
        "başına kullanıcının konusuyla eşleştiği anlamına gelmez. Başlık ile 'Ne "
        "karşılaştırıyor' açıklamasını semantik olarak değerlendir; gerçekten "
        "eşleşen yoksa hiçbirini önerme. Bunlar banka bilgisi değil: içlerinde oran, ücret "
        "ya da koşul yok. Rakamları yine banka uzmanlarından al. Kullanıcının "
        "sorduğu konuya gerçekten karşılık gelen TEK tabloyu öner ve yukarıdaki "
        "markdown linki AYNEN kopyala: başına alan adı EKLEME, adresi yeniden "
        "yazma, kısaltma."
    )


def _still_published(hits: list[dict]) -> list[dict]:
    """Drop candidates whose page would open with no table on it.

    The Qdrant collection is a build artifact: a point per table at the moment
    `dataprep.compare` indexed it. Whether that table still *draws* anything is
    decided at read time and moves with the date -- once every row on it has
    expired, `GET /api/compare-tables` stops listing it, and the assistant would
    be handing the reader a link to an empty page.

    Filtered here rather than by deleting points, because the condition is not a
    property of the point: a table with one row ending tomorrow is publishable
    today and not the day after, with nothing having been re-indexed. Deleting
    would be right for an hour and stale after that.
    """
    from api import compare_tables_pool as pool

    urls, now = pool.url_records(), pool.today()
    kept = []
    for hit in hits:
        table = pool.load_table(hit.get("id") or "")
        # Not in the pool at all: kept, deliberately. That is a stale index, not
        # an empty page, and this filter answers one question only -- "would this
        # page draw a table". Dropping on absence would also make the tool refuse
        # every hit whenever the pool is pointed elsewhere, which is a much
        # louder failure than the one it guards. A link to a table that does not
        # exist is already refused where it matters, in `api/agent.py`'s
        # `site_table_sources`, which rebuilds every link from the pool before a
        # source card is made.
        if table is not None and not pool.drawn_rows(table, urls, now):
            logger.info("table_directory dropped %r: every row expired or empty", hit.get("id"))
            continue
        kept.append(hit)
    return kept


def build_table_directory_tool() -> StructuredTool:
    def find_comparison_table(query: str, intent: str = "") -> str:
        # Over-fetched, because `_still_published` removes candidates *after*
        # ranking. Asking for exactly five and filtering would quietly return
        # three, and the model reads a short list as "the site has little on
        # this" rather than as a filter having run. `OVERFETCH` is generous
        # against the 2% of the pool that is currently unpublishable.
        hits = _still_published(
            search_tables(query, intent=intent, limit=RESULTS_PER_CALL * OVERFETCH)
        )[:RESULTS_PER_CALL]
        logger.info("table_directory query=%r candidates=%d no_score_cutoff",
                    query, len(hits))
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
