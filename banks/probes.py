"""One known-good call per bank capability, for the health check to replay.

These are deliberately boring: a product every bank really offers, a round
amount, and a term the bank really prices. The point is not coverage — the
verify scripts under docs/discovery/ cover every product — but to answer one
question each morning: does this capability still work at all?

Amounts and terms are chosen to sit well inside each bank's limits, so a probe
failing means the endpoint changed rather than that we drifted onto a boundary.
Hayat's 50 000 is its published minimum, not a preference.

Anything not listed here is not probed. A bank with an empty capability set
(Adil, T.O.M.) has nothing to probe and is skipped entirely.
"""

# category to ask products() for, per bank. The first one its catalogue serves.
PRODUCT_CATEGORY = {
    "kuveytturk": "finance",
    "albaraka": "finance",
    "vakif": "finance",
    "emlak": "finance",
    "dunya": "finance",
    "ziraat": "finance",
    "turkiyefinans": "finance",
    "hayat": "profit_share",
    "tom": "finance",
}

# product, amount, term in months.
FINANCE = {
    "kuveytturk": ("İhtiyaç Finansmanı", 100_000, 24),
    "albaraka": ("Eğitim Finansmanı", 100_000, 12),
    "vakif": ("İhtiyaç Finansmanı", 100_000, 24),
    "emlak": ("İhtiyaç Finansmanı", 100_000, 24),
    "dunya": ("Tüketici İhtiyaç", 100_000, 24),
    "ziraat": ("ARSA FINANSMANI", 100_000, 24),
    "tom": ("İhtiyaç Finansmanı", 10_000, 6),
    # Answers with a rate and no instalment, so the health check asserts on the
    # rate rather than the payment. 24 months sits inside its 19-24 band.
    "turkiyefinans": ("İhtiyaç Finansmanı", 100_000, 24),
}

# product, amount, term in DAYS, currency. Days everywhere, because that is what
# most of these endpoints actually count and the unit is never left implied.
PROFIT_SHARE = {
    "kuveytturk": ("Katılma Hesabı", 100_000, 31, "TRY"),
    "albaraka": ("Katılma Hesabı", 100_000, 90, "TRY"),
    "vakif": ("Katılma Hesabı", 100_000, 31, "TRY"),
    "emlak": ("Katılma Hesabı", 100_000, 31, "TRY"),
    "dunya": ("Standart Katılma Hesabı", 100_000, 31, "TRY"),
    # 50 000 is Hayat's hard floor: below it the endpoint answers zeros.
    "hayat": ("Katılma Hesabı", 50_000, 32, "TRY"),
}

# card, amount, instalments. Kept below every declared maximum, because two
# banks advertise more instalments than their calculator accepts.
CARD = {
    "kuveytturk": ("Sağlam Kart Troy", 10_000, 6),
    "vakif": ("Ferah Kart", 10_000, 3),
}

# source, target, amount.
CONVERT = {
    "kuveytturk": ("USD", "TRY", 1_000),
    "albaraka": ("USD", "TRY", 1_000),
    "vakif": ("USD", "TRY", 1_000),
    "dunya": ("USD", "TRY", 1_000),
    "hayat": ("USD", "TRY", 1_000),
}

# capability -> the table holding its probe.
BY_CAPABILITY = {
    "products": PRODUCT_CATEGORY,
    "finance": FINANCE,
    "profit_share": PROFIT_SHARE,
    "card": CARD,
    "convert": CONVERT,
    # rates takes no arguments, so every bank declaring it is probed the same
    # way and needs no entry.
    "rates": {},
    # mile_rates likewise takes no arguments.
    "mile_rates": {},
}


def missing() -> list[tuple[str, str]]:
    """Declared capabilities with no probe. Used by a test, so a new bank
    cannot be added to the registry and silently go unchecked."""
    from .providers import BANKS

    gaps = []
    for bank in BANKS:
        for capability in sorted(bank.capabilities):
            table = BY_CAPABILITY.get(capability)
            if table is None:
                gaps.append((bank.name, capability))
                continue
            # A no-argument probe (rates, mile_rates) is declared as an empty
            # table: every bank is called the same way and needs no per-bank row.
            if table == {}:
                continue
            if bank.name not in table:
                gaps.append((bank.name, capability))
    return gaps
