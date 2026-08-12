"""Detectors that decide whether extracted text is worth keeping.

All cheap, all measured against the crawled corpus, none of them an LLM. The
rule they serve is the repo's: guard against silent wrong answers. A PDF that
cannot be read must be reported as unread, never written out as a short document
that looks fine until someone quotes a profit rate from it.
"""

import re
import unicodedata

# Words so common in Turkish prose that their absence means the text is not
# Turkish -- or is not prose. Chosen to be frequent in bank documents
# specifically, and short enough to survive bad OCR.
_FUNCTION_WORDS = frozenset({
    "ve", "ile", "veya", "bu", "bir", "için", "olarak", "tarafından",
    "halinde", "kabul", "beyan", "eder", "üzere", "gerekli", "banka",
})

_TURKISH_LETTERS = set("çğıöşüÇĞİÖŞÜ")

# What a model says when it has been handed an image it cannot see. gpt-oss
# answers this way with HTTP 200 and no error, so the string is the only signal.
_BLINDNESS = (
    "can't see", "cannot see", "unable to see", "no image", "not able to see",
    "göremiyorum", "göremedim", "resim yok", "görsel yok",
)

# The model is told to write these rather than guess. Both are machine-readable
# on purpose: they turn "I could not read this" into a measurable quantity.
UNREADABLE = "[okunamadı]"
NO_TEXT = "[metin yok]"


def stamp_lines(pages: list[str], fraction: float = 0.8) -> set[str]:
    """Lines repeated on most pages: headers, footers, watermarks.

    Must be stripped before any per-page character count, or every scanned page
    looks like it has text. The 113-page scans carry a per-page
    `Doğrulama Kodu: <uuid>` stamp, which is exactly why the old crawler's
    40-character floor classified them as readable and saved the stamp as the
    document.
    """
    if len(pages) < 3:
        return set()
    counts: dict[str, int] = {}
    for page in pages:
        for line in {ln.strip() for ln in page.splitlines() if ln.strip()}:
            counts[line] = counts.get(line, 0) + 1
    threshold = len(pages) * fraction
    return {line for line, count in counts.items() if count >= threshold}


def strip_stamps(text: str, stamps: set[str]) -> str:
    """Remove the repeated furniture from one page."""
    if not stamps:
        return text
    kept = [ln for ln in text.splitlines() if ln.strip() not in stamps]
    return "\n".join(kept)


def unique_line_ratio(text: str, min_length: int = 20) -> float:
    """How much of the text is not a repeat of itself.

    The corpus median is 0.972. The documents `pypdf` mangled sat at 0.008-0.043
    because it returned the whole document body for every page. A low ratio means
    the extractor failed, not that the document is repetitive.

    Returns 1.0 when there is too little text to judge.
    """
    lines = [ln.strip() for ln in text.splitlines() if len(ln.strip()) >= min_length]
    if len(lines) < 5:
        return 1.0
    return len(set(lines)) / len(lines)


def turkish_score(text: str) -> float:
    """How plausibly Turkish this text is, from 0 to 1.

    Three signals, averaged: Turkish-specific letters, common function words,
    and the share of characters that are letters at all. Used both to catch OCR
    that produced character soup and to tag the genuinely English documents
    (Patriot Act, W8BEN, Wolfsberg) rather than treating them as failures.
    """
    sample = text[:8000]
    if len(sample.strip()) < 40:
        return 0.0

    letters = sum(1 for c in sample if c.isalpha())
    diacritics = sum(1 for c in sample if c in _TURKISH_LETTERS)
    diacritic_rate = min((diacritics / letters) * 25, 1.0) if letters else 0.0

    words = re.findall(r"\w+", sample.lower())
    hits = sum(1 for w in set(words) if w in _FUNCTION_WORDS)
    word_rate = min(hits / 6, 1.0)

    alpha_rate = letters / len(sample)

    return round((diacritic_rate + word_rate + min(alpha_rate * 1.5, 1.0)) / 3, 3)


def looks_blind(text: str) -> bool:
    """Whether a model answered as though it had been handed no image.

    HTTP 200 with a fluent refusal is the shape this fails in, so the string is
    the only thing that distinguishes it from a real answer.
    """
    lowered = text.lower()
    return any(marker in lowered for marker in _BLINDNESS)


def unreadable_ratio(text: str) -> float:
    """Share of lines the model marked as illegible."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return 0.0
    return sum(1 for ln in lines if UNREADABLE in ln) / len(lines)


def page_has_text(text: str, minimum: int = 100) -> bool:
    """Whether this page's text layer is real content rather than a stamp.

    The measured gap is not merely bimodal, it is disjoint: image-only pages
    yield 0-1 characters after stamp-stripping, text pages 269 and up. Anything
    in between does not occur, so the exact threshold is not delicate.
    """
    return len(text.strip()) >= minimum


def normalise(text: str) -> str:
    """NFC, expanded ligatures, tidy whitespace. What gets stored.

    NFC and not NFKD: some producers emit "ğ" as g + combining breve, and
    composing them is what makes two spellings of one document hash alike.
    `banks.parse.fold` is NFKD and strips punctuation -- it builds match keys,
    and running it over text meant for storage would destroy Turkish.
    """
    text = unicodedata.normalize("NFC", text)
    for ligature, expansion in (("ﬁ", "fi"), ("ﬂ", "fl"), ("ﬀ", "ff"),
                                ("ﬃ", "ffi"), ("ﬄ", "ffl"), ("ﬅ", "ft")):
        text = text.replace(ligature, expansion)
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r" *\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
