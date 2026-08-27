"""The user's scheduled agent runs, and the reports they produce."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: The list-shaped fields are validated here rather than in the router because
#: they arrive from two places -- a form and a language model -- and the rule is
#: the same for both.
MAX_WEEKDAY = 6
TITLE_CHARS = 160
MIN_CHECK_MINUTES = 5
MAX_CHECK_MINUTES = 10_080


class ConstantOperand(BaseModel):
    """A literal threshold on the right side of an alert."""

    source: Literal["constant"] = "constant"
    value: float


class BankRateOperand(BaseModel):
    """One buy/sell cell from a bank's live FX or precious-metal table."""

    source: Literal["bank_rate"] = "bank_rate"
    bank: str = Field(min_length=1)
    code: str = Field(min_length=2, description="Canonical code such as XAU, USD or XAG.")
    side: Literal["buy", "sell"]

    @field_validator("bank", "code", mode="before")
    @classmethod
    def clean_key(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("code")
    @classmethod
    def upper_code(cls, value: str) -> str:
        return value.upper()


class FinanceOperand(BaseModel):
    """One metric from a like-for-like live financing quote."""

    source: Literal["finance"] = "finance"
    bank: str = Field(min_length=1)
    family: str = Field(min_length=1, description="Comparison family, e.g. tasit-0km.")
    amount: float = Field(gt=0, description="Financing amount in TRY.")
    term_months: int = Field(gt=0, le=360)
    metric: Literal[
        "monthly_installment", "total_repayment", "profit_rate", "annual_cost_rate"
    ] = "monthly_installment"
    variant: str = Field(
        default="",
        description="Required only when a bank returns several variants for the family.",
    )

    @field_validator("bank", "family", "variant", mode="before")
    @classmethod
    def clean_text(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value


class ProfitShareOperand(BaseModel):
    """One metric from a live participation-account quote."""

    source: Literal["profit_share"] = "profit_share"
    bank: str = Field(min_length=1)
    family: str = Field(min_length=1)
    amount: float = Field(gt=0)
    term: int = Field(gt=0)
    term_unit: Literal["day", "month"] = "month"
    currency: str = Field(default="TRY", min_length=3, max_length=3)
    metric: Literal[
        "net_profit", "gross_profit", "net_annual_rate", "gross_annual_rate"
    ] = "net_profit"
    variant: str = ""

    @field_validator("bank", "family", "variant", mode="before")
    @classmethod
    def clean_text(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("currency")
    @classmethod
    def upper_currency(cls, value: str) -> str:
        return value.upper()


DynamicOperand = Annotated[
    FinanceOperand | BankRateOperand | ProfitShareOperand,
    Field(discriminator="source"),
]
ConditionOperand = Annotated[
    FinanceOperand | BankRateOperand | ProfitShareOperand | ConstantOperand,
    Field(discriminator="source"),
]


def _compatible_key(operand: DynamicOperand) -> tuple:
    """Everything that must match for two live figures to be comparable."""
    if isinstance(operand, BankRateOperand):
        return (operand.source, operand.code, operand.side)
    if isinstance(operand, FinanceOperand):
        return (
            operand.source,
            operand.family,
            operand.amount,
            operand.term_months,
            operand.metric,
            operand.variant,
        )
    return (
        operand.source,
        operand.family,
        operand.amount,
        operand.term,
        operand.term_unit,
        operand.currency,
        operand.metric,
        operand.variant,
    )


class ConditionSpec(BaseModel):
    """Versioned, deterministic condition evaluated without an LLM."""

    version: Literal[1] = 1
    left: DynamicOperand
    operator: Literal["lt", "lte", "gt", "gte"]
    right: ConditionOperand

    @model_validator(mode="after")
    def compatible_operands(self) -> "ConditionSpec":
        if not isinstance(self.right, ConstantOperand):
            if _compatible_key(self.left) != _compatible_key(self.right):
                raise ValueError(
                    "Two live operands must use the same product, inputs, metric and unit."
                )
            if self.left.bank == self.right.bank:
                raise ValueError("A bank cannot be compared with itself.")
        return self


def _clean_weekdays(value: object) -> list[int]:
    """Deduplicated, sorted, in range. Anything else dropped.

    Dropping rather than rejecting: a stray `7` from a model should not cost the
    six days it got right. `bool` is excluded explicitly -- it is an `int`
    subclass, and `True` would otherwise be stored as Tuesday.

    Mirrors `api/automations/schedule.py::valid_weekdays`, which is the authority
    when a schedule is *read*. This one keeps what is written clean, so the two
    never have to disagree about a row already in the table.
    """
    if not isinstance(value, (list, tuple, set)):
        return []
    return sorted(
        {
            item
            for item in value
            if isinstance(item, int)
            and not isinstance(item, bool)
            and 0 <= item <= MAX_WEEKDAY
        }
    )


class AutomationIn(BaseModel):
    """A new automation with its schedule given explicitly.

    The form's path. `POST /me/automations/describe` is the other one, where the
    schedule is read out of a sentence.
    """

    title: str = Field(min_length=1, max_length=TITLE_CHARS)
    prompt: str = Field(min_length=1)
    hour: int = Field(ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    weekdays: list[int] = Field(default_factory=list)
    web_search: bool = True
    email_enabled: bool = False
    email_format: Literal["pdf", "docx"] = "pdf"
    kind: Literal["scheduled_report", "condition_alert"] = "scheduled_report"
    condition: ConditionSpec | None = None
    interval_minutes: int | None = Field(
        default=None, ge=MIN_CHECK_MINUTES, le=MAX_CHECK_MINUTES
    )
    window_start_minute: int | None = Field(default=None, ge=0, le=1439)
    window_end_minute: int | None = Field(default=None, ge=0, le=1439)

    @field_validator("weekdays", mode="before")
    @classmethod
    def clean_weekdays(cls, value: object) -> list[int]:
        return _clean_weekdays(value)

    @model_validator(mode="after")
    def complete_kind(self) -> "AutomationIn":
        if self.kind == "condition_alert":
            if self.condition is None:
                raise ValueError("A condition alert needs a condition.")
        elif self.condition is not None:
            raise ValueError("A scheduled report cannot carry an alert condition.")
        if (self.window_start_minute is None) != (self.window_end_minute is None):
            raise ValueError("A time window needs both a start and an end.")
        if self.window_start_minute is not None and self.interval_minutes is None:
            raise ValueError("A time window requires an interval schedule.")
        if self.window_start_minute == self.window_end_minute and self.window_start_minute is not None:
            raise ValueError("A time window start and end cannot be equal.")
        return self


class AutomationDescribeIn(BaseModel):
    """Free text, plus whatever the user set by hand.

    A field left `None` is one the agent decides. A field with a value **wins**:
    the user who moved the hour picker did so after reading their own sentence,
    and a model's reading of "akşam" does not outrank that.
    """

    text: str = Field(min_length=1)
    hour: int | None = Field(default=None, ge=0, le=23)
    minute: int | None = Field(default=None, ge=0, le=59)
    #: `None` means "the agent decides"; `[]` means the user chose every day.
    #: Those are different answers, which is why this is nullable rather than
    #: defaulting to an empty list.
    weekdays: list[int] | None = None
    web_search: bool | None = None
    email_enabled: bool = False
    email_format: Literal["pdf", "docx"] = "pdf"
    interval_minutes: int | None = Field(
        default=None,
        ge=MIN_CHECK_MINUTES,
        le=MAX_CHECK_MINUTES,
        description="Optional user-selected frequency for any automation.",
    )
    window_start_minute: int | None = Field(default=None, ge=0, le=1439)
    window_end_minute: int | None = Field(default=None, ge=0, le=1439)

    @field_validator("weekdays", mode="before")
    @classmethod
    def clean_weekdays(cls, value: object) -> list[int] | None:
        return None if value is None else _clean_weekdays(value)

    @model_validator(mode="after")
    def complete_window(self) -> "AutomationDescribeIn":
        if (self.window_start_minute is None) != (self.window_end_minute is None):
            raise ValueError("A time window needs both a start and an end.")
        if self.window_start_minute is not None and self.interval_minutes is None:
            raise ValueError("A time window requires an interval schedule.")
        if self.window_start_minute == self.window_end_minute and self.window_start_minute is not None:
            raise ValueError("A time window start and end cannot be equal.")
        return self


class AutomationPatch(BaseModel):
    """A partial edit. Only the fields present are written."""

    title: str | None = Field(default=None, min_length=1, max_length=TITLE_CHARS)
    prompt: str | None = Field(default=None, min_length=1)
    hour: int | None = Field(default=None, ge=0, le=23)
    minute: int | None = Field(default=None, ge=0, le=59)
    weekdays: list[int] | None = None
    web_search: bool | None = None
    email_enabled: bool | None = None
    email_format: Literal["pdf", "docx"] | None = None
    enabled: bool | None = None
    interval_minutes: int | None = Field(
        default=None, ge=MIN_CHECK_MINUTES, le=MAX_CHECK_MINUTES
    )
    window_start_minute: int | None = Field(default=None, ge=0, le=1439)
    window_end_minute: int | None = Field(default=None, ge=0, le=1439)
    condition: ConditionSpec | None = None

    @field_validator("weekdays", mode="before")
    @classmethod
    def clean_weekdays(cls, value: object) -> list[int] | None:
        return None if value is None else _clean_weekdays(value)


class AutomationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    prompt: str
    hour: int
    minute: int
    weekdays: list[int]
    web_search: bool
    email_enabled: bool
    email_format: Literal["pdf", "docx"]
    kind: Literal["scheduled_report", "condition_alert"]
    condition: dict
    interval_minutes: int | None
    window_start_minute: int | None
    window_end_minute: int | None
    enabled: bool
    next_run_at: datetime
    last_run_at: datetime | None
    last_error: str
    last_condition_met: bool | None
    last_observation: dict
    last_triggered_at: datetime | None
    created_at: datetime


class ReportOut(BaseModel):
    """One report. `body` is markdown, rendered by the chat answer renderer."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    automation_id: uuid.UUID | None
    title: str
    body: str
    citations: list[dict]
    status: str
    error: str
    read_at: datetime | None
    created_at: datetime


class ReportSummary(BaseModel):
    """A report without its body, for the list and the notification menu.

    The body is the whole point of a report and also all of its weight -- a
    gold-price report with ten citations is several kilobytes of markdown. The
    notification menu polls on a timer and shows five lines; sending it every
    body would make the badge the most expensive request in the app.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    automation_id: uuid.UUID | None
    title: str
    status: str
    read_at: datetime | None
    created_at: datetime


class UnreadCount(BaseModel):
    unread: int = Field(description="Reports this user has not opened yet.")
