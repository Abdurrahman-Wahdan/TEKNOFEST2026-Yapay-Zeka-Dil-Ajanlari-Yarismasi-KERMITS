"""Base class for banks."""

import unicodedata
from abc import ABC, abstractmethod

from ..models import (
    CardInstallmentQuote,
    Conversion,
    FinanceQuote,
    ProfitShareQuote,
    Product,
    Rate,
)


class UnsupportedProduct(ValueError):
    """A bank does not offer, or does not publish a price for, what was asked.

    Subclasses ValueError deliberately: everywhere else in this project bad
    input raises ValueError with a message listing the valid alternatives, and
    this is the same thing. "This bank does not publish this" is a legitimate
    answer the agent must be able to give, so the message is written to be read
    by a person.
    """


def fold(text: str) -> str:
    """A key for matching Turkish product names typed any which way.

    Turkish casing does not round-trip through str.lower(): "İ".lower() leaves a
    combining dot, and a model asked for "IHTIYAC FINANSMANI" means the same
    thing as "İhtiyaç Finansmanı". So both sides are folded to bare ASCII
    letters and digits before comparing.
    """
    for pair in ("ıi", "İi", "şs", "Şs", "ğg", "Ğg", "çc", "Çc", "öo", "Öo", "üu", "Üu"):
        text = text.replace(pair[0], pair[1])
    stripped = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in stripped if c.isalnum())


class BaseBank(ABC):
    """One participation bank, behind the tools the agent binds.

    Add a bank by writing one module here and appending an instance to BANKS in
    __init__.py. No tool signature changes: `bank` is a parameter.

    Declare `capabilities` honestly. A bank that publishes no card calculator
    inherits the refusal below rather than returning an empty answer, because
    silent nothing is indistinguishable from a broken endpoint.
    """

    name: str = ""
    display_name: str = ""
    capabilities: frozenset[str] = frozenset()

    def __init__(self) -> None:
        # Product catalogues change about as often as the product range, so
        # they are fetched once per process. Cleared by clear_catalogue_cache().
        self._catalogue: dict[str, list[Product]] = {}

    @abstractmethod
    def products(self, category: str) -> list[Product]:
        """Every product the bank prices in a category, from its own catalogue.

        Categories: finance, profit_share, card, gold. Raises UnsupportedProduct
        for a category this bank does not have.
        """

    @abstractmethod
    def finance_quote(self, product: str, amount: float, term: int) -> FinanceQuote:
        """Instalment plan for a financing product. `product` is a code or a name."""

    @abstractmethod
    def profit_share_quote(
        self,
        product: str,
        amount: float,
        term: int,
        currency: str = "TRY",
        term_unit: str | None = None,
    ) -> ProfitShareQuote:
        """Profit share on a participation account.

        `term_unit` is "day" or "month". Left as None the bank is asked in the
        unit its own product most likely takes, falling back to the other one,
        and the quote reports which unit actually answered.
        """

    @abstractmethod
    def rates(self) -> list[Rate]:
        """The bank's published FX and precious-metal rates."""

    def card_installment_quote(
        self, card: str, amount: float, installments: int
    ) -> CardInstallmentQuote:
        """Instalment plan for a credit card purchase.

        Banks with no card calculator inherit this and refuse.
        """
        raise self._unsupported("a credit card instalment calculator")

    def convert(self, source: str, target: str, amount: float) -> Conversion:
        """Convert between currencies, or price grams of a precious metal.

        Banks with no converter inherit this and refuse.
        """
        raise self._unsupported("a currency converter")

    def find_product(self, category: str, query: str) -> Product:
        """Resolve a product code or a Turkish product name to a Product.

        Users ask for "ihtiyaç finansmanı", not SAGLIKFINANSMANI, and that
        mapping is bank-specific, so the model is never asked to know it.

        Raises:
            UnsupportedProduct: if nothing matches, or a name matches several
                products, listing what this bank actually offers.
        """
        available = self.products(category)
        wanted = fold(query)
        if not wanted:
            raise self._no_such_product(query, category, available)

        for product in available:
            if fold(product.code) == wanted:
                return product
        for product in available:
            if fold(product.name) == wanted:
                return product

        partial = [p for p in available if wanted in fold(p.name)]
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            names = ", ".join(p.name for p in partial)
            raise UnsupportedProduct(
                f"{query!r} matches several {self.display_name} products: {names}. "
                f"Ask for one of them by name."
            )
        raise self._no_such_product(query, category, available)

    def _unsupported(self, what: str) -> UnsupportedProduct:
        return UnsupportedProduct(
            f"{self.display_name} does not publish {what}. "
            f"It publishes: {', '.join(sorted(self.capabilities))}."
        )

    def _no_such_product(
        self, query: str, category: str, available: list[Product]
    ) -> UnsupportedProduct:
        names = ", ".join(f"{p.name} ({p.code})" for p in available)
        return UnsupportedProduct(
            f"{self.display_name} has no {category} product matching {query!r}. "
            f"Available: {names}."
        )
