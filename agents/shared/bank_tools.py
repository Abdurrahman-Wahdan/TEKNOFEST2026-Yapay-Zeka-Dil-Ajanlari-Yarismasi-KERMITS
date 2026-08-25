"""Create a bank specialist's constrained tool surface.

The public ``banks.tools`` API remains generic for existing callers.  This
module is deliberately separate: it binds a bank before LangChain sees a tool,
so a specialist cannot redirect a request to another bank.

Three kinds of tool are built here and they answer different questions:

    live endpoints   what the bank's calculator says right now -- a quote, a
                     rate, an instalment. Gated on ``bank.capabilities``,
                     because a bank that publishes no calculator must not be
                     given one.
    corpus retrieval what the bank has published -- campaign conditions,
                     eligibility rules, fee schedules, validity windows. Not
                     gated: every bank's site was crawled, and having no live
                     calculator has nothing to do with having no documents.
                     Built in ``corpus.search``.
    web research     what the bank's public page/PDF/image says now. Added only
                     when the request explicitly permits it, and bank-bound in
                     ``agents.shared.web_research``.

The retrieval tools are documentation, not live data, and the specialist prompt
says so: a figure read out of a published page is not a quote and must not be
reported with a live retrieval time.
"""

from typing import Callable

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field, model_validator

from banks.factory import get_bank
from banks import tools as generic
from banks.health import run as run_health
from corpus.search import build_bank_retrieval_tools
from corpus.sites import get_site

from .results import live_result
from .retrieval_memory import RetrievalMemory
from .web_research import build_bank_web_tools


# Public pages that expose or explain each bank's live calculators/feeds. The
# JSON service URL is an implementation detail and is often opaque; this is the
# stable, user-checkable bank page whose UI calls that service. A live result
# carries this page through the specialist and supervisor citation pipeline.
_LIVE_SOURCE_PAGES: dict[str, dict[str, tuple[str, str]]] = {
    "kuveytturk": {
        "default": (
            "https://www.kuveytturk.com.tr/hesaplama-araclari/",
            "Kuveyt Türk Hesaplama Araçları",
        ),
        "mile_earning_rates": (
            "https://milesandsmiles.kuveytturk.com.tr/",
            "Kuveyt Türk Miles&Smiles",
        ),
    },
    "albaraka": {
        "default": (
            "https://www.albaraka.com.tr/tr/hesaplama-araclari",
            "Albaraka Türk Hesaplama Araçları",
        ),
    },
    "vakif": {
        "default": (
            "https://www.vakifkatilim.com.tr/tr/yardimci-sayfalar/hesaplama-araclari",
            "Vakıf Katılım Hesaplama Araçları",
        ),
    },
    "emlak": {
        "default": (
            "https://www.emlakkatilim.com.tr/tr/hesaplama-araclari",
            "Emlak Katılım Hesaplama Araçları",
        ),
        "exchange_rates": (
            "https://www.emlakkatilim.com.tr/tr/tum-kurlarimiz",
            "Emlak Katılım Tüm Kurlarımız",
        ),
    },
    "dunya": {
        "default": (
            "https://dunyakatilim.com.tr/",
            "Dünya Katılım Hesaplama Araçları",
        ),
        "exchange_rates": (
            "https://dunyakatilim.com.tr/gunluk-kurlar",
            "Dünya Katılım Günlük Kurlar",
        ),
    },
    "ziraat": {
        "default": (
            "https://www.ziraatkatilim.com.tr/anasayfa",
            "Ziraat Katılım Hesaplama Araçları",
        ),
    },
    "turkiyefinans": {
        "default": (
            "https://www.turkiyefinans.com.tr/tr-tr/hesaplama-araclari/Sayfalar/hesaplama-araclari.aspx",
            "Türkiye Finans Hesaplama Araçları",
        ),
        "card_installment_quote": (
            "https://www.turkiyefinans.com.tr/tr-tr/hesaplama-araclari/Sayfalar/taksitle-hesaplama-araci.aspx",
            "Türkiye Finans Taksitle Hesaplama Aracı",
        ),
    },
    "hayat": {
        "default": (
            "https://hayatfinans.com.tr/",
            "Hayat Finans Hesaplama Araçları",
        ),
    },
    "tom": {
        "default": (
            "https://www.tombank.com.tr/hesaplama-araclari.html",
            "T.O.M. Katılım Hesaplama Araçları",
        ),
    },
}


def _live_source(bank: str, tool: str) -> tuple[str, str]:
    sources = _LIVE_SOURCE_PAGES.get(bank, {})
    return sources.get(tool) or sources.get("default") or (
        get_site(bank).base,
        f"{get_site(bank).display_name} resmi sitesi",
    )


class ProductsInput(BaseModel):
    category: str = Field(description="finance, profit_share, or card")


class FinanceInput(BaseModel):
    product: str = Field(description="The product's Turkish name or code.")
    amount: float = Field(gt=0, description="Amount in Turkish lira.")
    term_months: int = Field(gt=0, description="Term in months.")
    include_schedule: bool = Field(default=False, description="Include the full payment plan.")


class CustomRateFinanceInput(FinanceInput):
    monthly_profit_rate: float | None = Field(
        gt=0,
        le=100,
        description=(
            "Required. The customer-supplied monthly profit rate as a percentage, "
            "or null when requesting the bank's live rate. Infer this from the user "
            "request and always include the field."
        ),
    )


class ProfitShareInput(BaseModel):
    product: str = Field(description="The account's Turkish name or code.")
    amount: float = Field(gt=0)
    term_months: int = Field(default=0, ge=0)
    term_days: int = Field(default=0, ge=0)
    currency: str = Field(default="TRY")

    @model_validator(mode="after")
    def one_term_unit(self) -> "ProfitShareInput":
        if bool(self.term_months) == bool(self.term_days):
            raise ValueError("Give exactly one of term_months or term_days.")
        return self


class RatesInput(BaseModel):
    codes: list[str] | None = Field(default=None, description="Optional ISO currency/metal codes.")


class CardInput(BaseModel):
    card: str = Field(description="The card's Turkish name or code.")
    amount: float = Field(gt=0)
    installments: int = Field(gt=0)


class ConversionInput(BaseModel):
    source: str = Field(description="Source currency code, such as TRY or USD.")
    target: str = Field(description="Target currency code, such as TRY or XAU.")
    amount: float = Field(gt=0)


class MileInput(BaseModel):
    card: str | None = None
    category: str | None = None


class EmptyInput(BaseModel):
    pass


def _tool(
    name: str,
    description: str,
    schema: type[BaseModel],
    func: Callable,
) -> BaseTool:
    return StructuredTool.from_function(
        func=func, name=name, description=description, args_schema=schema
    )


def build_bank_tools(
    bank_name: str,
    *,
    enforced_monthly_profit_rate: float | None = None,
    retrieval: RetrievalMemory | None = None,
    web_research_enabled: bool = False,
) -> list[BaseTool]:
    """The named bank's tools: the live endpoints it truthfully supports, plus
    corpus retrieval over what it has published.

    Args:
        retrieval: where this specialist's keep/drop decisions are recorded, so
            ``RetrievalPruning`` can act on them. Defaults to a private one,
            which leaves the tools working and the decisions inert -- fine for a
            caller that only wants to inspect the tool surface, wrong for a
            running specialist, which always passes its own.
    """
    bank = get_bank(bank_name)
    capabilities = bank.capabilities
    tools: list[BaseTool] = []

    def run_live(tool: str, call: Callable[[], object]) -> str:
        source_url, source_title = _live_source(bank.name, tool)
        return live_result(
            bank.name,
            tool,
            call,
            source_url=source_url,
            source_title=source_title,
        )

    if "products" in capabilities:
        tools.append(_tool(
            "list_products",
            f"List {bank.display_name}'s live products in one category.",
            ProductsInput,
            lambda category: run_live("list_products", lambda: [
                generic._product(item) for item in bank.products(category)
            ]),
        ))
    if "finance" in capabilities:
        finance_schema = (
            CustomRateFinanceInput
            if "monthly_profit_rate" in bank.finance_input_capabilities
            else FinanceInput
        )

        def finance(
            product: str,
            amount: float,
            term_months: int,
            include_schedule: bool = False,
            monthly_profit_rate: float | None = None,
        ) -> str:
            def quote_data():
                # The supervisor may have extracted an explicit customer rate
                # from natural language.  Do not rely on a second model to
                # repeat it exactly in its tool arguments: bind it into this
                # specialist's one delegated turn instead.
                effective_rate = (
                    monthly_profit_rate
                    if monthly_profit_rate is not None
                    else enforced_monthly_profit_rate
                )
                quote = bank.finance_quote(
                    product,
                    amount,
                    term_months,
                    **(
                        {"monthly_profit_rate": effective_rate}
                        if effective_rate is not None
                        else {}
                    ),
                )
                if (
                    effective_rate is not None
                    and abs(float(quote.profit_rate) - float(effective_rate)) > 0.005
                ):
                    raise ValueError(
                        "The live calculator did not apply the requested monthly profit "
                        f"rate ({effective_rate}%). It returned {quote.profit_rate}% instead."
                    )
                data = generic._finance(quote, include_schedule)
                if effective_rate is not None:
                    data["pricing_basis"] = "customer_supplied_monthly_profit_rate"
                    data["requested_monthly_profit_rate"] = effective_rate
                return data

            return run_live("finance_quote", quote_data)

        tools.append(_tool(
            "finance_quote",
            (
                f"Get a live financing quote from {bank.display_name}. Use only for this bank. "
                + (
                    "This calculator also supports an optional customer-supplied monthly profit-rate scenario."
                    if "monthly_profit_rate" in bank.finance_input_capabilities
                    else ""
                )
            ),
            finance_schema,
            finance,
        ))
    if "profit_share" in capabilities:
        tools.append(_tool(
            "profit_share_quote",
            f"Get a live profit-share quote from {bank.display_name}. Use only for this bank.",
            ProfitShareInput,
            lambda product, amount, term_months=0, term_days=0, currency="TRY": run_live(
                "profit_share_quote", lambda: generic._profit_share(
                    bank.profit_share_quote(
                        product,
                        amount,
                        term_months or term_days,
                        currency,
                        "month" if term_months else "day",
                    )
                )
            ),
        ))
    if "rates" in capabilities:
        tools.append(_tool(
            "exchange_rates",
            f"Get {bank.display_name}'s live FX and precious-metal rates.",
            RatesInput,
            lambda codes=None: run_live("exchange_rates", lambda: [
                generic._rate(row) for row in bank.find_rates(codes)
            ]),
        ))
    if "card" in capabilities:
        tools.append(_tool(
            "card_installment_quote",
            f"Get a live card instalment quote from {bank.display_name}.",
            CardInput,
            lambda card, amount, installments: run_live(
                "card_installment_quote", lambda: generic._card(
                    bank.card_installment_quote(card, amount, installments)
                )
            ),
        ))
    if "convert" in capabilities:
        tools.append(_tool(
            "convert_currency",
            f"Convert currencies using {bank.display_name}'s live calculator or rate feed.",
            ConversionInput,
            lambda source, target, amount: run_live(
                "convert_currency", lambda: generic._conversion(
                    bank.convert(source, target, amount)
                )
            ),
        ))
    if "mile_rates" in capabilities:
        def miles(card: str | None = None, category: str | None = None) -> str:
            def call():
                rows = bank.mile_rates()
                if card:
                    rows = [row for row in rows if card.lower() in row.card.lower()]
                if category:
                    rows = [row for row in rows if category.lower() in row.category.lower()]
                return [generic._mile(row) for row in rows]
            return run_live("mile_earning_rates", call)
        tools.append(_tool("mile_earning_rates", f"Get {bank.display_name}'s live reward rates.", MileInput, miles))

    # What this bank has published, as opposed to what its calculator answers.
    #
    # `corpus_slug`, not `bank_name`: the store stamps chunks with the name the
    # crawl used ("vakifkatilim"), which differs from the provider key ("vakif")
    # for seven of the ten banks. A filter on the wrong name matches nothing and
    # raises nothing, so getting this wrong would leave those seven specialists
    # searching forever and being told their bank has published nothing.
    memory = retrieval or RetrievalMemory()
    tools.extend(build_bank_retrieval_tools(
        get_site(bank_name).corpus_slug, memory.marked, memory.discarded))

    # On-demand network access is request permission, not a permanent agent
    # capability. When the Advanced toggle is off these tools do not exist in
    # the specialist's schema, so a prompt cannot bypass the user's choice.
    if web_research_enabled:
        tools.extend(build_bank_web_tools(bank_name))

    # Health is intentionally scoped to this provider and is a diagnostic tool,
    # never a way for one specialist to inspect another bank.
    tools.append(_tool(
        "check_live_endpoint_health",
        f"Check whether {bank.display_name}'s advertised live endpoints respond now.",
        EmptyInput,
        lambda: run_live("check_live_endpoint_health", lambda: run_health(
            banks=[bank.name]
        ).as_dict()),
    ))
    return tools
