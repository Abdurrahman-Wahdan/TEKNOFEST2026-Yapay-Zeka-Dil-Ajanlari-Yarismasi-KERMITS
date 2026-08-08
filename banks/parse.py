"""Turning what banks send into numbers and match keys.

One parser, not one per provider. Six of the ten banks answer with Turkish
formatted strings and four with JSON numbers, and several mix the two inside a
single response, so every provider goes through here.
"""

import re
import unicodedata

_NUMERIC = re.compile(r"[^\d,.-]")

_TURKISH = {
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ç": "c", "Ç": "c", "ö": "o", "Ö": "o", "ü": "u", "Ü": "u",
}


def money(text) -> float:
    """'6.684,28 TL' -> 6684.28, and a number straight through.

    Turkish formatting: the dot groups thousands and the comma is the decimal
    separator. The currency suffix varies by product, so comparing the string
    against '0,00 TL' silently accepts '0,00 USD' as a real value — parse first,
    then compare numerically.
    """
    if text is None:
        return 0.0
    if isinstance(text, bool):
        return 0.0
    if isinstance(text, (int, float)):
        return float(text)
    cleaned = _NUMERIC.sub("", str(text)).replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def rate(text) -> float:
    """'% 36.731684' -> 36.731684, '%31,80' -> 31.80, 4.11 -> 4.11.

    Rates are not formatted like amounts, and the two arrive in the same
    response: Albaraka states GrossRate with a dot for its decimals and
    IncomeTax with a comma. Parsing a rate with money() turns 36.731684 into
    36731684, which is why these are separate functions rather than one clever
    one.
    """
    if text is None:
        return 0.0
    if isinstance(text, bool):
        return 0.0
    if isinstance(text, (int, float)):
        return float(text)
    cleaned = _NUMERIC.sub("", str(text))
    # A comma can only be the decimal separator, so dots must be grouping.
    # Without one, a lone dot is the decimal point.
    if "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def fold(text: str) -> str:
    """A key for matching Turkish product names typed any which way.

    Turkish casing does not round-trip through str.lower(): "İ".lower() leaves a
    combining dot, and a model asked for "IHTIYAC FINANSMANI" means the same
    thing as "İhtiyaç Finansmanı". So both sides are folded to bare ASCII
    letters and digits before comparing.
    """
    for turkish, ascii_ in _TURKISH.items():
        text = text.replace(turkish, ascii_)
    stripped = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in stripped if c.isalnum())


_DAY_WORDS = {"day", "days", "gun", "gunluk", "gunler"}
_MONTH_WORDS = {"month", "months", "ay", "aylik", "aylar"}


def term_unit(value) -> str | None:
    """Normalise a term unit, in English or Turkish, to "day" or "month".

    A Turkish-speaking model asked about "12 ay" may well send "ay", and
    rejecting it would refuse a question the bank can answer.

    Raises:
        ValueError: on a unit that is neither, listing what is accepted.
    """
    if not value:
        return None
    folded = fold(str(value))
    if folded in _DAY_WORDS:
        return "day"
    if folded in _MONTH_WORDS:
        return "month"
    raise ValueError(
        f"term_unit must say days or months — day, days, gun, month, months or "
        f"ay. Got {value!r}."
    )
