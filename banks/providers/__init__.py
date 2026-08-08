"""Bank registry.

To add a bank: write one module here and append an instance to BANKS. No tool
signature changes — `bank` is a parameter of every tool, not part of its name.

All ten banks in the list are registered, including the two with nothing to
call. Adil and T.O.M. are providers with an empty `capabilities` set, because
"this bank does not publish a calculator" is a correct answer for a user and the
agent can only give it if the registry says so.
"""

from .adil import Adil
from .albaraka import Albaraka
from .base import BaseBank, UnsupportedProduct
from .dunya import Dunya
from .emlak import Emlak
from .hayat import Hayat
from .kuveytturk import KuveytTurk
from .tom import Tom
from .turkiyefinans import TurkiyeFinans
from .vakif import Vakif
from .ziraat import Ziraat

BANKS: list[BaseBank] = [
    KuveytTurk(),
    Albaraka(),
    Vakif(),
    Emlak(),
    Dunya(),
    Ziraat(),
    TurkiyeFinans(),
    Hayat(),
    Tom(),
    Adil(),
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
    "Adil",
    "Albaraka",
    "BaseBank",
    "Dunya",
    "Emlak",
    "Hayat",
    "KuveytTurk",
    "Tom",
    "TurkiyeFinans",
    "UnsupportedProduct",
    "Vakif",
    "Ziraat",
    "clear_catalogue_cache",
    "get_provider",
]
