"""Adil Katılım — no public calculator.

A real provider with no capabilities, not an absent bank. Probed with a browser
on the correct domain (`adilkatilim.com.tr`; the bank list's `adilkatilim.com`
is NXDOMAIN): zero inputs, zero selects and zero backend calls across the whole
site.

Registering it means the agent can answer "this bank does not publish a
calculator", which is a correct and useful thing to tell a user. Every method
refuses through BaseBank, so no request is ever made — there is nothing to call.

Contract in docs/discovery/captured/adil.md.
"""

from .base import BaseBank


class Adil(BaseBank):
    name = "adil"
    display_name = "Adil Katılım Bankası"
    capabilities = frozenset()
    transport = "none"
    notes = (
        "Adil Katılım publishes no public calculator at all — its site has no "
        "calculator inputs and makes no pricing requests — so there is no live "
        "figure to quote."
    )
