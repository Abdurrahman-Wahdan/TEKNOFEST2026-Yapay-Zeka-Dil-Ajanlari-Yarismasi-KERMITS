"""When a campaign starts and ends.

The single biggest correctness risk in the system. Of the 422 crawled campaigns
carrying an explicit date range, **323 had already expired on the day they were
crawled** — so a retriever with no date filter will confidently recommend offers
that ended two years ago.

86% of campaign pages expose a parseable date, but only Kuveyt Türk labels it:
410 of its 442 campaigns carry "Kampanya Tarihleri", and the other nine banks
bury the date in prose ("31 Aralık 2026 tarihine kadar"). So there are three
patterns, and which one matched is recorded, because a date lifted out of a
sentence is weaker evidence than one under a heading.
"""

import re
from datetime import date

# Turkish month names, including the accusative/locative forms that appear in
# running text ("Aralık'ta", "Ocak ayında").
MONTHS = {
    "ocak": 1, "şubat": 2, "subat": 2, "mart": 3, "nisan": 4,
    "mayıs": 5, "mayis": 5, "haziran": 6, "temmuz": 7,
    "ağustos": 8, "agustos": 8, "eylül": 9, "eylul": 9,
    "ekim": 10, "kasım": 11, "kasim": 11, "aralık": 12, "aralik": 12,
}
_MONTH_ALTERNATION = "|".join(sorted(MONTHS, key=len, reverse=True))

# 6.08.2026 or 06/08/2026. The day and month may be one or two digits: the
# label form runs the date straight onto the heading with no space, as in
# "**Kampanya Tarihleri**6.08.2026 - 31.12.2026".
_NUMERIC = r"(\d{1,2})[./](\d{1,2})[./](\d{4})"
_DASH = r"\s*[-–—]\s*"

_LABELLED = re.compile(
    r"Kampanya\s*Tarih\w*\s*[:\-]?\s*\**\s*" + _NUMERIC + _DASH + _NUMERIC,
    re.IGNORECASE)
_RANGE = re.compile(_NUMERIC + _DASH + _NUMERIC)
_LONG = re.compile(r"(\d{1,2})\s+(" + _MONTH_ALTERNATION + r")\s+(\d{4})",
                   re.IGNORECASE)
# "1 Nisan - 31 Aralık 2026": the start states no year of its own, the end's
# year covers both. Common enough that without it every such campaign loses its
# start date and looks open-ended.
_SHARED_YEAR = re.compile(
    r"(\d{1,2})\s+(" + _MONTH_ALTERNATION + r")\s*[-–—]\s*"
    r"(\d{1,2})\s+(" + _MONTH_ALTERNATION + r")\s+(\d{4})", re.IGNORECASE)
# "... 31 Aralık 2026 tarihine kadar" / "31.12.2026 tarihine kadar geçerlidir"
_UNTIL_LONG = re.compile(
    r"(\d{1,2})\s+(" + _MONTH_ALTERNATION + r")\s+(\d{4})\s*"
    r"(?:tarihine|tarihinde|'e|'a|a|e)?\s*kadar", re.IGNORECASE)
_UNTIL_NUMERIC = re.compile(_NUMERIC + r"\s*(?:tarihine|tarihinde)?\s*kadar",
                            re.IGNORECASE)


def _iso(day: str | int, month: str | int, year: str | int) -> str:
    """An ISO date, or "" when the parts do not make a real day."""
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except (ValueError, TypeError):
        return ""


def _iso_long(day: str, month_name: str, year: str) -> str:
    return _iso(day, MONTHS.get(month_name.lower(), 0), year)


def extract(text: str) -> tuple[str, str, str]:
    """Find a campaign's validity dates.

    Returns:
        `(start, end, source)` as ISO dates and one of "label", "range",
        "prose" or "". `source` is kept on the document so a downstream filter
        can weigh a labelled date more heavily than one read out of a sentence.
    """
    if not text:
        return "", "", ""

    match = _LABELLED.search(text)
    if match:
        start = _iso(*match.group(1, 2, 3))
        end = _iso(*match.group(4, 5, 6))
        if start or end:
            return start, end, "label"

    match = _RANGE.search(text)
    if match:
        start = _iso(*match.group(1, 2, 3))
        end = _iso(*match.group(4, 5, 6))
        if start and end and start <= end:
            return start, end, "range"

    match = _SHARED_YEAR.search(text)
    if match:
        day_one, month_one, day_two, month_two, year = match.groups()
        start = _iso_long(day_one, month_one, year)
        end = _iso_long(day_two, month_two, year)
        # A campaign running into the next year writes the start's year out, so
        # a reversed pair here means the two halves are not one range.
        if start and end and start <= end:
            return start, end, "prose"

    # A range written out in words. Three traps, all measured on real pages:
    #
    #   "1 Nisan - 31 Aralık 2026"  -- the start carries no year, so scanning
    #       finds the end first and the pair comes out reversed;
    #   the same date written twice, which is not a range at all and used to
    #       produce 34 campaigns that began and ended on one day;
    #   the heading repeating the date the body states.
    #
    # Sorting distinct values answers all three: earliest is the start, latest
    # the end, and a single distinct date is an end with no stated start. Only
    # the first few are considered, so a date further down the page -- a
    # publication stamp, an unrelated footnote -- cannot widen the range.
    distinct: list[str] = []
    for parts in _LONG.findall(text):
        value = _iso_long(*parts)
        if value and value not in distinct:
            distinct.append(value)
        if len(distinct) == 2:
            break
    if len(distinct) == 2:
        # Sorted, because a yearless start makes the scanner meet the end first.
        # The first two *in document order*, not the smallest and largest on the
        # page: a publication stamp in a footer would otherwise stretch a
        # one-month campaign back to 2019.
        return min(distinct), max(distinct), "prose"

    # An end date only. Common, and the one that actually matters: a campaign
    # with no stated start is still live or expired on its end date alone.
    match = _UNTIL_LONG.search(text)
    if match:
        end = _iso_long(*match.group(1, 2, 3))
        if end:
            return "", end, "prose"

    match = _UNTIL_NUMERIC.search(text)
    if match:
        end = _iso(*match.group(1, 2, 3))
        if end:
            return "", end, "prose"

    if distinct:
        return "", distinct[0], "prose"

    return "", "", ""


def is_active(end: str, today: str | None = None) -> bool:
    """Whether a campaign is still running.

    Computed, never stored. `end >= today` changes as the day changes while the
    document does not, so a stored flag would make yesterday's artifact lie by
    tomorrow.
    """
    if not end:
        # No end date is not evidence of expiry. Refusing to show these would
        # hide campaigns whose page simply never stated a deadline.
        return True
    return end >= (today or date.today().isoformat())
