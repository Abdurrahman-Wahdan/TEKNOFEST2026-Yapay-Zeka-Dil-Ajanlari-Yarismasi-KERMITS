"""Reading the comparison-table pool, and making it renderable.

The files on disk (`_tables/*.json`) are what an offline agent traversal wrote:
Turkish column names it chose per table, every value a string, and a per-source
record of where each fact came from and how long it is good for. None of that is
in the shape a table renderer wants, and three of the differences are not
cosmetic -- they are the difference between a correct table and a wrong one.

**Whether a bank offers the thing at all.** The producer answers this in a column
it names itself, and across the pool it has used 93 different names for it
(`Sunum Durumu`, `Kampanya Durumu`, `sunuluyor_mu`, ...), put it first in only two
thirds of tables, and in 32 tables left it out entirely -- there, a bank that does
not offer the product has the literal string `sunulmuyor` in every cell instead,
which is the convention the previous dataset used throughout. Reading that in the
browser would mean shipping a list of 93 column names to the client. It is read
here instead, once, into `offers`.

**How long a fact is good for.** Every source carries `gecerlilik_baslangic` /
`gecerlilik_bitis` as ISO dates, and the producer *also* writes a human-readable
`Geçerlilik` column summarising them. The column is not reliable -- 606 rows print
"-" while their own sources carry dates, and of the 194 that do show a window only
100 agree with the sources they came from. So the column is dropped and a window
is derived from the source dates instead, which is the same data the summary was
made from, minus the summarising.

**`_url_havuzu.json` is optional supplementary metadata.** The transferred 2026-08
tables already carry their source URLs and validity fields inside each source
record, so citations never depend on the pool. When a migration also ships the
central registry its newer date is preferred; otherwise the table copy is the
authoritative fallback.

Nothing here decides how any of it *looks*. `routers/compare_tables.py` turns what
this module returns into columns and rows.
"""

import datetime
import glob
import json
import logging
import os
import threading
import unicodedata
from pathlib import Path

logger = logging.getLogger(__name__)

# The dataset is a build artifact of `dataprep.compare`, refreshed by re-running
# that pipeline rather than by anything in this process. Overridable because
# swapping datasets is otherwise a code edit: point it at a different extraction
# to compare the two without a deploy.
ROOT = Path(
    os.environ.get("COMPARE_TABLES_DIR")
    or Path(__file__).resolve().parent.parent / "TF26_data" / "data" / "_tables"
)

URL_POOL = "_url_havuzu.json"

CATEGORIES = {"ürün", "kampanya"}

# `_tables` uses the crawl's site-folder slugs; the rest of the app (bank logos,
# GET /api/banks, contract.ts's KNOWN_BANKS) uses the shorter provider keys.
# Bridged here, once, rather than at every call site.
BANK_KEY = {
    "kuveytturk": "kuveytturk",
    "albaraka": "albaraka",
    "vakifkatilim": "vakif",
    "emlakkatilim": "emlak",
    "dunyakatilim": "dunya",
    "ziraatkatilim": "ziraat",
    "turkiyefinans": "turkiyefinans",
    "hayatfinans": "hayat",
    "tombank": "tom",
    "adilkatilim": "adil",
}

# --- the producer's sentinels ------------------------------------------------
# A cell the producer could not fill. It writes all four spellings -- the new
# extraction uses "" and "-" interchangeably (9136 and 4001 cells), the previous
# one used "belirtilmemiş". They all mean the same thing and the table has one
# way of drawing it, so they are all resolved to None here rather than leaving
# the browser to render `-` as a literal dash next to a real em dash.
BLANK = {"", "-", "—", "–", "belirtilmemiş", "belirtilmemis"}

# A bank that does not offer the thing. Kept out of `BLANK` because it is a
# *fact*, not an absence, and it is what `offers` is read from.
NOT_OFFERED = {
    "sunulmuyor",
    "sunulmamaktadır",
    "sunmuyor",
    "bulunmuyor",
    "yok",
    "yoktur",
    "hayır",
    "hayir",
    "mevcut değil",
}
OFFERED = {
    "sunuluyor",
    "sunulmaktadır",
    "sunulmakta",
    "var",
    "vardır",
    "evet",
    "mevcut",
    "aktif",
}

# The validity verdict, in the same hardcoded Turkish as `Banka` -- this whole
# data domain is Turkish and has no English side to translate from.
ACTIVE = "aktif"
EXPIRED = "süresi geçmiş"
SCHEDULED = "başlamadı"
UNKNOWN = "bilinmiyor"

# The producer's own validity column. Dropped rather than shown: it is a summary
# of the source dates below, and it disagrees with them (see the module docstring).
PRODUCER_VALIDITY_COLUMN = "Geçerlilik"

# The 2026-08 extraction stamps a *per-cell* window beside every column it fills:
# `Kâr Oranı` is followed by `Kâr Oranı (Geçerlilik)` holding `01/08/2026 -
# 30/09/2026`. Three facts about it decide how it is read here.
#
# **It is a date, not a column.** Drawn as declared it doubles every table --
# 3022 extra columns across the pool, 20 where the previous dataset had 11 --
# and 62% of them are blank, because most pages are permanent product pages with
# no date to stamp. So the suffix is recognised as metadata and is not rendered.
#
# **It is the only copy.** These windows were read out of the page text and never
# written back into `cell_sources`: 189 rows carry a date here and nowhere else.
# Reading validity from the sources alone would silently drop them. They remain
# attached to their individual claims: an expired campaign cell is suppressed
# without expiring unrelated permanent facts in the same bank row.
#
# **The producer stamps its own summary column too**, which is where the literal
# `Geçerlilik (Geçerlilik)` in all 402 tables comes from. It is `-` in every one
# of its 4020 cells; the suffix rule removes it along with the rest.
VALIDITY_SUFFIX = " (Geçerlilik)"


def is_validity_column(column: str) -> bool:
    """Whether this column holds a per-cell window rather than content."""
    return column.endswith(VALIDITY_SUFFIX)


def _window(value: object) -> tuple[datetime.date | None, datetime.date | None]:
    """A `dd/mm/yyyy - dd/mm/yyyy` cell as dates, `?` on either side for open.

    Anything else is "no window" rather than an error: this runs over every cell
    of every table, and one malformed stamp must not take a page down. Measured
    on the current pool, all 2249 non-blank stamps parse.
    """
    if not isinstance(value, str) or value.strip() in BLANK:
        return None, None
    halves = value.split(" - ")
    if len(halves) != 2:
        logger.warning("Unparseable per-cell validity window %r.", value)
        return None, None
    out = []
    for half in halves:
        half = half.strip()
        if half == "?":
            out.append(None)
            continue
        try:
            day, month, year = half.split("/")
            out.append(datetime.date(int(year), int(month), int(day)))
        except (ValueError, TypeError):
            logger.warning("Unparseable per-cell validity window %r.", value)
            return None, None
    return out[0], out[1]


def _fold(value: object) -> str:
    """Lowercased and trimmed, for matching a cell against a sentinel set.

    `str.lower()` and not a Turkish-locale fold: every sentinel above is written
    without a dotted/dotless I, so the two agree, and Python has no locale-aware
    lower to reach for anyway."""
    return value.strip().lower() if isinstance(value, str) else ""


def _iso(value: object) -> datetime.date | None:
    """A `YYYY-MM-DD` string as a date. Every dated source in the pool uses that
    format -- 1754 values, no exceptions -- so anything else is a producer bug
    and becomes "no date" rather than a 500."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.date.fromisoformat(value.strip())
    except ValueError:
        logger.warning("Unparseable validity date %r in the table pool.", value)
        return None


def today() -> datetime.date:
    """Today in Istanbul, which is where every one of these campaigns runs.

    A campaign ending "today" ends today in Turkey; deciding that from the
    server's own clock would expire it a day early or late for anyone hosted
    elsewhere. `format.ts`'s `daysUntil` draws the same line on the client."""
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3))).date()


# --- loading ------------------------------------------------------------------
_lock = threading.Lock()
_cache: dict[str, object] = {"signature": None, "tables": [], "urls": {}}


def _signature() -> tuple:
    """What the directory looks like right now.

    Stat-ing 400 files costs a millisecond or two and parsing them costs
    considerably more, which is the whole point: the previous version re-read and
    re-parsed every file on every request. Size and mtime together catch a
    pipeline re-run, a single edited table, and a whole directory swapped out."""
    out = []
    for path in sorted(glob.glob(str(ROOT / "*.json"))):
        try:
            stat = os.stat(path)
        except OSError:
            continue
        out.append((path, stat.st_mtime_ns, stat.st_size))
    return tuple(out)


def _read_urls() -> dict[str, dict]:
    """`_url_havuzu.json` flattened to url -> record.

    It is keyed by bank and then by url, but a url belongs to exactly one bank,
    so the outer level carries no information a lookup needs."""
    path = ROOT / URL_POOL
    if not path.exists():
        # Not fatal. Every source carries its own copy of these fields; the pool
        # is the better copy, not the only one. A dataset shipped without it
        # still renders, just with the 763 unstamped statuses left unstamped.
        logger.warning("No %s beside the tables — validity falls back to each table's own copy.", URL_POOL)
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Could not read the url pool %s", path)
        return {}
    return {url: rec for urls in raw.values() for url, rec in urls.items()}


def _load() -> tuple[list[dict], dict[str, dict]]:
    """Every table file and the url pool, parsed, cached until the files change."""
    signature = _signature()
    with _lock:
        if _cache["signature"] == signature:
            return _cache["tables"], _cache["urls"]  # type: ignore[return-value]

        tables = []
        for path, _mtime, _size in signature:
            if Path(path).name.startswith("_"):
                continue
            try:
                tables.append(json.loads(Path(path).read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                logger.exception("Could not read table %s", path)

        urls = _read_urls()
        _cache.update(signature=signature, tables=tables, urls=urls)
        logger.info("Loaded %d comparison tables and %d source urls from %s", len(tables), len(urls), ROOT)
        return tables, urls


def all_tables() -> list[dict]:
    return _load()[0]


def url_records() -> dict[str, dict]:
    """`_url_havuzu.json`, keyed by url — the better copy of every source's
    validity dates. Empty when the dataset shipped without it."""
    return _load()[1]


def table_path(table_id: str) -> Path:
    """Resolve an id to its file, refusing anything outside the directory — the
    same belt-and-braces containment check `components.py` uses for its
    fixtures."""
    return (ROOT / f"{table_id}.json").resolve()


def load_table(table_id: str) -> dict | None:
    """One table by id, matched on its Unicode normal form.

    Every id in this pool is Turkish, and Turkish ids exist in two byte
    sequences that mean the same thing: `ğ` is one code point in NFC and two in
    NFD. macOS writes filenames in NFD while the id *inside* each file is NFC,
    so `kredi-kartı-doğum-günü-kampanyaları` read off the disk does not equal
    the same name read out of the JSON. Comparing raw, half of a random sample
    of ten tables 404'd.

    The browser never hits this — it round-trips ids that came from the list
    endpoint, which reads them out of the files — but anything deriving an id
    from a filename does, and which form arrives is a property of whatever
    filesystem the dataset was last copied across rather than of the data."""
    path = table_path(table_id)
    if not path.is_relative_to(ROOT.resolve()):
        raise ValueError("Bad table id.")
    wanted = unicodedata.normalize("NFC", table_id)
    for table in all_tables():
        if unicodedata.normalize("NFC", table.get("id", "")) == wanted:
            return table
    return None


# --- offering status ----------------------------------------------------------
def status_column(table: dict) -> str | None:
    """The column that answers "does this bank offer it at all", if there is one.

    **`sunulmuyor` means two different things in this data, and telling them
    apart is the whole job here.** At row level it is the verdict: a bank that
    does not offer the product has it in every cell. At cell level it is "this
    field does not apply" -- a campaign whose points never expire has
    `sunulmuyor` under `Puan Son Kullanma Tarihi` while very much being offered.

    So a column only qualifies when *every* filled cell in it is a verdict and at
    least one of them is a positive one. The second half is what separates a
    status column from a sparse field: `Puan Son Kullanma Tarihi` reads
    `sunulmuyor` for nine banks out of ten and nothing else, which is a perfect
    score on the first test and exactly the column that must not be trusted.
    Requiring a `Sunuluyor` somewhere in it means a real status column -- which
    always has banks on both sides, or it would not be a comparison -- passes,
    and a field nobody fills does not.

    A qualifying column also says nothing but the verdict, so the caller drops it
    rather than printing "Sunuluyor" once per row. That is why the bar is this
    high: getting it wrong deletes a column, and a near miss used to delete the
    product names out of `Ürün Adı`.

    None means no column answers it and the caller falls back to reading the row
    as a whole, which is the previous dataset's convention and still the only
    signal in 172 tables."""
    for column in table.get("columns", []):
        if column == PRODUCER_VALIDITY_COLUMN or is_validity_column(column):
            continue
        filled = [
            _fold(values.get(column))
            for values in table.get("rows", {}).values()
            if _fold(values.get(column)) not in BLANK
        ]
        if not filled:
            continue
        if all(v in NOT_OFFERED or v in OFFERED for v in filled) and any(v in OFFERED for v in filled):
            return column
    return None


def offers(values: dict, column: str | None) -> bool | None:
    """Whether this bank offers the thing. None when nothing says either way.

    None is not "no". A row we cannot classify stays in the table, because
    hiding a bank the data never actually ruled out is the one failure here that
    the reader cannot see or correct.

    With a status column the verdict is read straight off it. Without one -- or
    where that bank left it blank -- the row answers as a whole, and only a row
    whose every filled cell is a `sunulmuyor` is a bank offering nothing. One
    such cell among real content is a field that does not apply, not a verdict:
    reading it as one is what put six banks that run this campaign into the
    "does not offer it" list."""
    if column is not None:
        verdict = _fold(values.get(column))
        if verdict in NOT_OFFERED:
            return False
        if verdict in OFFERED:
            return True
        # Blank in the one column that was supposed to answer: fall through to
        # the row rather than giving up on the question.

    # Content columns only. `Geçerlilik` is not one: it is the producer's date
    # summary, it is written for every row including the empty ones, and one
    # `? - 31/12/2026` in it is enough to make a row of nothing but `sunulmuyor`
    # look like a row with something in it.
    #
    # A per-cell `... (Geçerlilik)` stamp is not content either, and for the same
    # reason: it is written beside a cell whether or not that cell says anything,
    # so a row of nothing but `sunulmuyor` with one dated stamp would read as a
    # row with something in it.
    filled = [
        _fold(v)
        for key, v in values.items()
        if key not in (PRODUCER_VALIDITY_COLUMN, column)
        and not is_validity_column(key)
        and _fold(v) not in BLANK
    ]
    if not filled:
        return None
    return False if all(v in NOT_OFFERED for v in filled) else True


# --- validity -----------------------------------------------------------------
def validity(sources: list[dict], urls: dict[str, dict], now: datetime.date):
    """This row's validity window and what it means today.

    Returns `(valid_from, valid_to, verdict)` with the dates as ISO strings.

    The window is the *widest* one its sources describe -- earliest start, latest
    end -- and that is a deliberate choice rather than an average. A row often
    cites several pages, and where their windows differ it is usually because
    they are different offers supporting the same claim. Taking the widest means
    a row is only ever called expired when every page behind it has expired,
    which is the direction to be wrong in: marking a live offer dead is a visible
    error, and leaving a dead one unmarked is the status quo.

    The verdict is recomputed from the dates rather than read from the pool's own
    `validity_status`. The two agree exactly on today's data -- all 82
    `suresi_gecmis` really have ended, all 886 `gecerli` really are live -- but
    the stamp was written when the crawl ran and the dates keep being true
    afterwards. Recomputing also fills the 1373 sources that were never stamped."""
    starts: list[datetime.date] = []
    ends: list[datetime.date] = []
    for source in sources:
        record = urls.get(source.get("url") or "") or {}
        start = _iso(record.get("gecerlilik_baslangic")) or _iso(source.get("gecerlilik_baslangic"))
        end = _iso(record.get("gecerlilik_bitis")) or _iso(source.get("gecerlilik_bitis"))
        if start:
            starts.append(start)
        if end:
            ends.append(end)

    valid_from = min(starts) if starts else None
    valid_to = max(ends) if ends else None

    if valid_to is not None and valid_to < now:
        verdict = EXPIRED
    elif valid_from is not None and valid_from > now:
        verdict = SCHEDULED
    elif valid_from is not None or valid_to is not None:
        verdict = ACTIVE
    else:
        verdict = UNKNOWN

    return (
        valid_from.isoformat() if valid_from else None,
        valid_to.isoformat() if valid_to else None,
        verdict,
    )


def cell_validity(values: dict, column: str, now: datetime.date):
    """Validity of one claim, from its adjacent ``(Geçerlilik)`` field.

    A row may combine a permanent product description with a short campaign or
    fee. Folding all those windows into one row date made an expired cell look
    current whenever any other source ended later. Per-cell validity is kept at
    the same granularity as the claim instead.
    """
    start, end = _window(values.get(f"{column}{VALIDITY_SUFFIX}"))
    if end is not None and end < now:
        verdict = EXPIRED
    elif start is not None and start > now:
        verdict = SCHEDULED
    elif start is not None or end is not None:
        verdict = ACTIVE
    else:
        verdict = UNKNOWN
    return (
        start.isoformat() if start else None,
        end.isoformat() if end else None,
        verdict,
    )


def current_values(values: dict, now: datetime.date) -> dict:
    """A row copy with only individually expired claims removed.

    Scheduled claims remain visible: they are real upcoming information and
    their start date travels as a cell note. Only claims whose own end date has
    passed are suppressed. The original migrated files are never rewritten.
    """
    out = dict(values)
    for column in tuple(values):
        if is_validity_column(column) or column == PRODUCER_VALIDITY_COLUMN:
            continue
        if cell_validity(values, column, now)[2] == EXPIRED:
            out[column] = None
    return out


def window_note(valid_from: str | None, valid_to: str | None) -> str:
    """The window as one line, for the hover on the verdict.

    An open end is a real answer and is written as one: a campaign with a start
    and no published end is not the same as a campaign we know nothing about, and
    both would otherwise read as a bare `aktif` chip."""
    if not valid_from and not valid_to:
        return ""
    start = _turkish(valid_from) if valid_from else "?"
    end = _turkish(valid_to) if valid_to else "?"
    return f"{start} – {end}"


def _turkish(iso: str) -> str:
    date = _iso(iso)
    return date.strftime("%d.%m.%Y") if date else iso


def cell(value: object) -> str | None:
    """One cell as the wire wants it: a string, or None for "not found".

    Every sentinel spelling collapses to None here so the client has one thing to
    draw for an absent value. `sunulmuyor` collapses too -- by the time a cell is
    being rendered, `offers` has already carried that fact, and repeating it in
    every column of the row is what made a not-offering bank read as a row of
    content rather than a row of nothing."""
    if not isinstance(value, str):
        return None
    folded = _fold(value)
    if folded in BLANK or folded in NOT_OFFERED:
        return None
    return value.strip()


# --- which rows a table actually draws -----------------------------------------
#
# Three kinds of row never reach the table body, and each is excluded for its own
# reason. Gathered here rather than in the router because `list_tables` and the
# agent's table directory need the same answer, and a table whose every row is
# excluded is a page with nothing on it -- which neither should offer.
#
# `offers is False`   the bank does not offer it; drawn in its own card instead.
# no content          nothing was found; drawn in its own card instead.
# EXPIRED             every source behind it has ended (user decision, 2026-08-27:
#                     an expired offer is not worth comparing, so it is dropped
#                     rather than carded -- the reader cannot act on it).
#
# Recomputed per request rather than cached against the file signature, because
# only the third depends on today's date: a row live this morning is expired
# tomorrow, and a table can empty out with no file having changed.
def row_has_content(values: dict, status: str | None) -> bool:
    """Whether this row says anything the table body could show."""
    return any(
        cell(value) is not None
        for key, value in values.items()
        if key not in (PRODUCER_VALIDITY_COLUMN, status) and not is_validity_column(key)
    )


def drawn_rows(table: dict, urls: dict[str, dict], now: datetime.date) -> list[str]:
    """The banks whose rows this table would actually draw, in file order."""
    status = status_column(table)
    out = []
    for bank, values in (table.get("rows") or {}).items():
        effective = current_values(values, now)
        if offers(effective, status) is False:
            continue
        if not row_has_content(effective, status):
            continue
        if validity(table.get("sources", {}).get(bank) or [], urls, now)[2] == EXPIRED:
            continue
        out.append(bank)
    return out
