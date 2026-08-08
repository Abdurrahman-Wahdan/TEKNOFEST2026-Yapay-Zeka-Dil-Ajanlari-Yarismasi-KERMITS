"""Bank registry.

To add a bank: write one module here and append an instance to BANKS. No tool
signature changes — `bank` is a parameter of every tool, not part of its name.
"""

from .albaraka import Albaraka
from .base import BaseBank, UnsupportedProduct
from .kuveytturk import KuveytTurk

BANKS: list[BaseBank] = [
    KuveytTurk(),
    Albaraka(),
]


def get_provider(name: str) -> BaseBank:
    """Find a bank by name.

    Raises:
        ValueError: if unknown, listing what is available.
    """
    for bank in BANKS:
        if bank.name == name.lower():
            return bank

    available = ", ".join(b.name for b in BANKS)
    raise ValueError(f"Unknown bank: {name!r}. Available: {available}")


def clear_catalogue_cache() -> None:
    """Drop every cached product catalogue. Lets tests start clean."""
    for bank in BANKS:
        bank._catalogue.clear()


__all__ = [
    "BANKS",
    "Albaraka",
    "BaseBank",
    "KuveytTurk",
    "UnsupportedProduct",
    "clear_catalogue_cache",
    "get_provider",
]
