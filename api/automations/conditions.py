"""Deterministic evaluation of typed live-banking alert conditions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from banks import clock, compare
from banks.factory import get_bank
from banks.source_pages import live_source

from ..schemas.automations import (
    BankRateOperand,
    ConditionSpec,
    ConstantOperand,
    FinanceOperand,
    ProfitShareOperand,
)


@dataclass
class ConditionResult:
    matched: bool
    body: str
    observations: dict
    citations: list[dict] = field(default_factory=list)


_OPERATORS: dict[str, Callable[[float, float], bool]] = {
    "lt": lambda left, right: left < right,
    "lte": lambda left, right: left <= right,
    "gt": lambda left, right: left > right,
    "gte": lambda left, right: left >= right,
}
_OPERATOR_TEXT = {"lt": "<", "lte": "≤", "gt": ">", "gte": "≥"}


def _money(value: float) -> str:
    return f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _citation(observation: dict) -> dict:
    return {
        "score": 1.0,
        "cite_url": observation["source_url"],
        "text": (
            f"{observation['label']}: {observation['display_value']} "
            f"({observation['checked_at']})"
        ),
        "bank": observation.get("bank", ""),
        "title": observation["source_title"],
        "doc_kind": "live_endpoint",
        "source_type": "live_endpoint",
        "from_vision": False,
    }


class _Resolver:
    """Resolve at most two operands while sharing like-for-like comparisons."""

    def __init__(self, spec: ConditionSpec) -> None:
        self.checked_at = clock.stamp_tr()
        self._finance: dict[tuple, object] = {}
        self._profit: dict[tuple, object] = {}
        operands = [spec.left]
        if not isinstance(spec.right, ConstantOperand):
            operands.append(spec.right)
        self._finance_banks: dict[tuple, list[str]] = {}
        self._profit_banks: dict[tuple, list[str]] = {}
        for operand in operands:
            if isinstance(operand, FinanceOperand):
                key = (operand.family, operand.amount, operand.term_months)
                self._finance_banks.setdefault(key, []).append(operand.bank)
            elif isinstance(operand, ProfitShareOperand):
                key = (
                    operand.family,
                    operand.amount,
                    operand.term,
                    operand.term_unit,
                    operand.currency,
                )
                self._profit_banks.setdefault(key, []).append(operand.bank)

    def resolve(self, operand) -> dict:
        if isinstance(operand, FinanceOperand):
            return self._resolve_finance(operand)
        if isinstance(operand, ProfitShareOperand):
            return self._resolve_profit(operand)
        if isinstance(operand, BankRateOperand):
            return self._resolve_rate(operand)
        raise ValueError("A constant is not a live operand.")

    def _resolve_finance(self, operand: FinanceOperand) -> dict:
        key = (operand.family, operand.amount, operand.term_months)
        comparison = self._finance.get(key)
        if comparison is None:
            comparison = compare.finance(
                operand.family,
                operand.amount,
                operand.term_months,
                sorted(set(self._finance_banks[key])),
            )
            self._finance[key] = comparison
        matches = [
            quote
            for quote in comparison.quotes
            if quote.bank == operand.bank
            and (not operand.variant or quote.variant == operand.variant)
        ]
        if len(matches) != 1:
            problem = next(
                (item.detail for item in comparison.unavailable if item.bank == operand.bank),
                "The bank returned no unique matching quote.",
            )
            raise ValueError(f"{get_bank(operand.bank).display_name}: {problem}")
        quote = matches[0]
        values = {
            "monthly_installment": quote.installment,
            "total_repayment": quote.total,
            "profit_rate": quote.profit_rate,
            "annual_cost_rate": quote.annual_cost_rate,
        }
        value = values[operand.metric]
        if value is None:
            raise ValueError(
                f"{get_bank(operand.bank).display_name} does not publish {operand.metric}."
            )
        percent = operand.metric in {"profit_rate", "annual_cost_rate"}
        label = f"{get_bank(operand.bank).display_name} — {quote.product.name}"
        return self._observation(
            bank=operand.bank,
            value=float(value),
            label=label,
            display_value=f"%{_money(float(value))}" if percent else f"{_money(float(value))} TL",
            tool="finance_quote",
            details={
                "source": operand.source,
                "family": operand.family,
                "metric": operand.metric,
                "amount": operand.amount,
                "term_months": operand.term_months,
                "product": quote.product.name,
                "variant": quote.variant,
                "derived": quote.derived,
            },
        )

    def _resolve_profit(self, operand: ProfitShareOperand) -> dict:
        key = (
            operand.family,
            operand.amount,
            operand.term,
            operand.term_unit,
            operand.currency,
        )
        comparison = self._profit.get(key)
        if comparison is None:
            comparison = compare.profit_share(
                operand.family,
                operand.amount,
                operand.term,
                operand.term_unit,
                operand.currency,
                sorted(set(self._profit_banks[key])),
            )
            self._profit[key] = comparison
        matches = [
            quote
            for quote in comparison.quotes
            if quote.bank == operand.bank
            and (not operand.variant or quote.variant == operand.variant)
        ]
        if len(matches) != 1:
            problem = next(
                (item.detail for item in comparison.unavailable if item.bank == operand.bank),
                "The bank returned no unique matching quote.",
            )
            raise ValueError(f"{get_bank(operand.bank).display_name}: {problem}")
        quote = matches[0]
        values = {
            "net_profit": quote.net_profit,
            "gross_profit": quote.gross_profit,
            "net_annual_rate": quote.net_annual_rate,
            "gross_annual_rate": quote.gross_annual_rate,
        }
        value = values[operand.metric]
        if value is None:
            raise ValueError(
                f"{get_bank(operand.bank).display_name} does not publish {operand.metric}."
            )
        percent = operand.metric.endswith("annual_rate")
        label = f"{get_bank(operand.bank).display_name} — {quote.product.name}"
        unit = "%" if percent else operand.currency
        return self._observation(
            bank=operand.bank,
            value=float(value),
            label=label,
            display_value=(
                f"%{_money(float(value))}" if percent else f"{_money(float(value))} {unit}"
            ),
            tool="profit_share_quote",
            details={
                "source": operand.source,
                "family": operand.family,
                "metric": operand.metric,
                "amount": operand.amount,
                "term": operand.term,
                "term_unit": operand.term_unit,
                "currency": operand.currency,
                "product": quote.product.name,
                "variant": quote.variant,
            },
        )

    def _resolve_rate(self, operand: BankRateOperand) -> dict:
        bank = get_bank(operand.bank)
        rows = bank.find_rates([operand.code])
        if len(rows) != 1:
            raise ValueError(f"{bank.display_name} does not publish {operand.code}.")
        row = rows[0]
        value = float(getattr(row, operand.side))
        label = f"{bank.display_name} — {row.name} {operand.side}"
        return self._observation(
            bank=operand.bank,
            value=value,
            label=label,
            display_value=f"{_money(value)} {row.quote_currency}",
            tool="exchange_rates",
            details={
                "source": operand.source,
                "code": operand.code,
                "side": operand.side,
                "unit": row.unit,
                "quote_currency": row.quote_currency,
                "as_of": row.as_of,
                "derived": row.derived,
            },
        )

    def _observation(
        self,
        *,
        bank: str,
        value: float,
        label: str,
        display_value: str,
        tool: str,
        details: dict,
    ) -> dict:
        source_url, source_title = live_source(bank, tool)
        if not source_url:
            raise ValueError(f"No public source page is configured for {bank}.")
        return {
            **details,
            "bank": bank,
            "value": value,
            "label": label,
            "display_value": display_value,
            "checked_at": self.checked_at,
            "source_url": source_url,
            "source_title": source_title,
        }


def evaluate_condition(spec: ConditionSpec) -> ConditionResult:
    """Evaluate once and return a notification-ready snapshot."""
    resolver = _Resolver(spec)
    left = resolver.resolve(spec.left)
    if isinstance(spec.right, ConstantOperand):
        threshold = float(spec.right.value)
        if isinstance(spec.left, FinanceOperand):
            threshold_display = (
                f"%{_money(threshold)}"
                if spec.left.metric in {"profit_rate", "annual_cost_rate"}
                else f"{_money(threshold)} TL"
            )
        elif isinstance(spec.left, ProfitShareOperand):
            threshold_display = (
                f"%{_money(threshold)}"
                if spec.left.metric.endswith("annual_rate")
                else f"{_money(threshold)} {spec.left.currency}"
            )
        else:
            threshold_display = f"{_money(threshold)} {left['quote_currency']}"
        right = {
            "source": "constant",
            "value": threshold,
            "label": "Eşik değer",
            "display_value": threshold_display,
        }
        citations = [_citation(left)]
    else:
        right = resolver.resolve(spec.right)
        if left["source"] == "bank_rate" and (
            left["unit"], left["quote_currency"]
        ) != (right["unit"], right["quote_currency"]):
            raise ValueError(
                "The two bank rates use different units or quote currencies."
            )
        citations = [_citation(left), _citation(right)]

    matched = _OPERATORS[spec.operator](left["value"], right["value"])
    symbol = _OPERATOR_TEXT[spec.operator]
    body = "\n".join(
        [
            "## Alarm koşulu gerçekleşti",
            "",
            f"- **{left['label']}:** {left['display_value']}",
            f"- **{right['label']}:** {right['display_value']}",
            f"- **Koşul:** {left['display_value']} {symbol} {right['display_value']}",
            f"- **Kontrol zamanı:** {resolver.checked_at}",
            "",
            "Değerler canlı banka kaynaklarından aynı kontrol sırasında alınmıştır.",
        ]
    )
    return ConditionResult(
        matched=matched,
        body=body,
        observations={
            "version": 1,
            "checked_at": resolver.checked_at,
            "matched": matched,
            "operator": spec.operator,
            "left": left,
            "right": right,
        },
        citations=citations,
    )
