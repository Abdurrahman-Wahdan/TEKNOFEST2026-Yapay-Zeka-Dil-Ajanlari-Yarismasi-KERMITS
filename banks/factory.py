"""Bank factory.

    from banks import get_bank, list_banks

    list_banks()                                        # ["kuveytturk", "albaraka"]
    bank = get_bank("kuveytturk")
    bank.products("finance")                            # from the live catalogue
    bank.finance_quote("ihtiyaç finansmanı", 100000, 24)
"""

import logging

from . import status
from .providers import BANKS, get_provider
from .providers.base import BaseBank

logger = logging.getLogger(__name__)


def get_bank(name: str) -> BaseBank:
    """Get a bank by name.

    Args:
        name: A bank name as list_banks() reports it.

    Raises:
        ValueError: if the bank is unknown, listing what is available.
    """
    bank = get_provider(name)
    logger.debug("Resolved bank %s", bank.name)
    return bank


def list_banks() -> dict[str, dict]:
    """Every known bank, what it publishes, and why it publishes no more.

    All ten are here, including the two with nothing to call. Capabilities are
    declared per bank and are honest: a bank missing a capability does not
    publish it, which is a legitimate answer rather than a failure. `notes`
    carries the reason where there is one worth giving — Adil has nothing to
    integrate, while T.O.M. needs only a credential, and the same user-facing
    answer today has different remedies.
    """
    listing = {}
    for bank in BANKS:
        entry = {
            "display_name": bank.display_name,
            "publishes": sorted(bank.capabilities),
            "finance_input_capabilities": sorted(bank.finance_input_capabilities),
            "notes": bank.notes,
        }
        # What the last check found broken. Read from the cached status file, so
        # it costs nothing and lets the agent route around a bank before
        # spending a call on it.
        down = sorted(
            capability for capability in bank.capabilities
            if status.outage(bank.name, capability)
        )
        if down:
            entry["maintenance"] = down
        listing[bank.name] = entry
    return listing
