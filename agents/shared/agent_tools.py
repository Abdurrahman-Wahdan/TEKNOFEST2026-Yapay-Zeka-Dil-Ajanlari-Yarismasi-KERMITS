"""Adapters that make private bank agents callable by the main agent."""

import json
import logging
import re

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from banks.factory import get_bank

from .registry import SpecialistSpec
from .specialists import build_specialist, specialist_thread_id
from .runtime import AgentContext

logger = logging.getLogger(__name__)


class DelegateInput(BaseModel):
    request: str = Field(
        description="A complete, bank-specific request including all known amounts, terms, products, and currencies."
    )
    monthly_profit_rate: float | None = Field(
        default=None,
        gt=0,
        le=100,
        description=(
            "Customer-supplied monthly profit rate for a financing scenario, if the user "
            "explicitly requested one. Do not use a bank's normal live rate instead."
        ),
    )
    web_research_required: bool = Field(
        default=False,
        description=(
            "Set true only when the user explicitly requests web/internet search, "
            "verification on a public page, every available source, or exhaustive "
            "online research. Do not set true merely because coverage includes all "
            "or every bank, product, or campaign (for example 'her banka' or 'tüm "
            "bankalar'). This requires search_bank_web at least once; indexed "
            "retrieval alone is insufficient."
        ),
    )


_URL = re.compile(r"https?://[^\s)\]>\"']+")
_MARKDOWN_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_POINT_ID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)
_LIVE_ENDPOINT_TOOLS = {
    "list_products", "finance_quote", "profit_share_quote", "exchange_rates",
    "card_installment_quote", "convert_currency", "mile_earning_rates",
}
_EVIDENCE_MARKER = "TF26_TOOL_EVIDENCE (machine-preserved from actual specialist calls):"


def source_key(url: str) -> str:
    """Normalize only harmless presentation differences for source matching."""
    return url.strip().rstrip(".,;:!?").rstrip("/")


def cited_sources_from_text(text: str) -> dict[str, str]:
    """Return URLs the model actually cited, keyed for exact-tool intersection."""
    cited: dict[str, str] = {}
    for title, url in _MARKDOWN_LINK.findall(text):
        cited[source_key(url)] = " ".join(title.split())
    for url in _URL.findall(text):
        cited.setdefault(source_key(url), "")
    return cited


def _used_source(
    *,
    url: str,
    cited: dict[str, str],
    title: str = "",
    source_type: str,
    provenance: str,
) -> dict | None:
    label = cited.get(source_key(url))
    if label is None:
        return None
    if _POINT_ID.fullmatch(label) or label.casefold().startswith("point_id"):
        label = ""
    return {
        "url": url,
        "title": label or title,
        "source_type": source_type,
        "provenance": provenance,
    }


def _tool_evidence(messages: list, cited: dict[str, str] | None = None) -> list[dict]:
    """A compact ledger derived from actual calls, never specialist prose.

    LangChain subagents can use a tool correctly and still omit its evidence in
    their final response. The supervisor sees only that response, so the
    adapter preserves the operational facts here at the privacy boundary while
    still withholding full tool payloads and private message history.
    """
    ledger = []
    cited = cited or {}
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        content = message.content if isinstance(message.content, str) else str(message.content)
        evidence: dict = {"tool": message.name or "unknown"}
        try:
            payload = json.loads(content)
        except (TypeError, json.JSONDecodeError):
            payload = None

        if isinstance(payload, dict):
            for key in (
                "bank", "source_type", "retrieved_at", "status", "http_status",
                "message",
            ):
                value = payload.get(key)
                if value not in (None, "", []):
                    evidence[key] = value
            if message.name in _LIVE_ENDPOINT_TOOLS and "source_type" not in evidence:
                evidence["source_type"] = "live_endpoint"
            used_sources: list[dict] = []
            if message.name == "search_bank_web":
                evidence["result_count"] = len(payload.get("results") or [])
                for row in (payload.get("results") or []):
                    if not isinstance(row, dict) or not row.get("url"):
                        continue
                    source = _used_source(
                        url=str(row["url"]),
                        title=str(row.get("title") or ""),
                        cited=cited,
                        source_type="web_search",
                        provenance="live_web",
                    )
                    if source:
                        used_sources.append(source)
            elif (
                message.name == "read_bank_source"
                and payload.get("status") == "ok"
                and payload.get("url")
            ):
                source = _used_source(
                    url=str(payload["url"]),
                    title=str(payload.get("title") or ""),
                    cited=cited,
                    source_type=str(payload.get("source_type") or "live_web_source"),
                    provenance="live_web",
                )
                if source:
                    used_sources.append(source)
            if used_sources:
                evidence["used_sources"] = used_sources
            if payload.get("status") != "ok":
                failed_url = payload.get("requested_url") or payload.get("url")
                if failed_url:
                    evidence["requested_url"] = failed_url
        else:
            evidence["status"] = "invoked"
            if message.name in {"search_bank", "expand_chunk", "read_full_page"}:
                evidence["source_type"] = "indexed_document"
            urls = list(dict.fromkeys(_URL.findall(content)))
            used_sources = [
                source
                for url in urls
                if (source := _used_source(
                    url=url,
                    cited=cited,
                    source_type="indexed_document",
                    provenance="knowledge_base",
                ))
            ]
            if used_sources:
                evidence["used_sources"] = used_sources[:8]
        ledger.append(evidence)
    return ledger


def used_sources_from_tool_message(message: ToolMessage) -> list[dict]:
    """Return claim-used sources from a filtered specialist handoff.

    Raw nested tool messages are intentionally ignored: they contain every
    search hit before the specialist decides which evidence supports its
    answer. Only the supervisor-facing ``ask_<bank>`` handoff contains the
    machine-intersected ``used_sources`` ledger.
    """
    content = message.content if isinstance(message.content, str) else str(message.content)
    if _EVIDENCE_MARKER not in content:
        return []
    _, _, encoded = content.rpartition(_EVIDENCE_MARKER)
    try:
        decoded = json.loads(encoded.strip())
    except (TypeError, json.JSONDecodeError):
        decoded = []
    evidence = (
        [row for row in decoded if isinstance(row, dict)]
        if isinstance(decoded, list)
        else []
    )
    handoff_bank = (
        str(message.name).removeprefix("ask_")
        if str(message.name or "").startswith("ask_")
        else ""
    )

    sources: list[dict] = []
    for row in evidence:
        for source in row.get("used_sources") or []:
            if not isinstance(source, dict) or not source.get("url"):
                continue
            sources.append({
                "url": source["url"],
                "title": source.get("title") or "",
                "bank": row.get("bank") or handoff_bank,
                "source_type": source.get("source_type") or "",
                "provenance": source.get("provenance") or "",
            })
    return sources


def web_sources_from_tool_message(message: ToolMessage) -> list[dict]:
    """Backward-compatible web-only view of the used-source handoff."""
    return [
        source for source in used_sources_from_tool_message(message)
        if source.get("provenance") == "live_web"
    ]


def _final_text(result: dict, evidence_messages: list | None = None) -> str:
    messages = result.get("messages") or []
    if not messages:
        return "The bank specialist returned no result."
    content = messages[-1].content
    final = content if isinstance(content, str) else str(content)
    evidence = _tool_evidence(
        messages if evidence_messages is None else evidence_messages,
        cited=cited_sources_from_text(final),
    )
    if not evidence:
        return final
    return (
        final
        + f"\n\n{_EVIDENCE_MARKER}\n"
        + json.dumps(evidence, ensure_ascii=False, separators=(",", ":"))
    )


def build_specialist_tool(spec: SpecialistSpec) -> BaseTool:
    """Wrap one specialist without exposing its tools or message history."""
    bank = get_bank(spec.bank)

    def delegate(
        request: str,
        runtime: ToolRuntime[AgentContext],
        monthly_profit_rate: float | None = None,
        web_research_required: bool = False,
    ) -> str:
        session_id = runtime.context.get("session_id")
        web_search_enabled = bool(runtime.context.get("web_search_enabled", False))
        if not session_id:
            return "The bank specialist cannot run because the chat session is missing."
        logger.info(
            "bank_specialist delegated bank=%s web_search_enabled=%s web_research_required=%s",
            spec.bank,
            web_search_enabled,
            web_research_required,
        )
        if web_research_required and not web_search_enabled:
            return (
                f"{spec.display_name} web research was explicitly requested, but Web "
                "search is disabled for this turn. Ask the user to enable Web search "
                "in Advanced; indexed retrieval does not satisfy this request."
            )
        if (
            monthly_profit_rate is not None
            and "monthly_profit_rate" not in bank.finance_input_capabilities
        ):
            return (
                f"{spec.display_name} unavailable: its live financing calculator does not "
                "accept a customer-supplied monthly profit rate. Do not substitute the "
                "bank's standard live rate for this scenario."
            )
        try:
            # This is intentionally blocking. LangChain's tool node waits for
            # this callable to return, while the specialist's tunnel-aware model
            # refreshes and retries internally. The main agent therefore sees
            # either the specialist's final response or a terminal failure after
            # the configured retry window, never an in-progress placeholder.
            # The parent's callbacks are carried across, and this is the only
            # reason anything can see what a specialist spends. A config built
            # from scratch here *replaces* the caller's rather than extending
            # it, so the handlers attached to the supervisor's run never reach
            # the specialist's model calls. Measured: with a fresh config the
            # inner call was invisible to the parent's usage handler; carrying
            # the callbacks through, it was counted. Ten specialists were
            # spending tokens that nothing could observe.
            #
            # `thread_id` is still replaced on purpose: the specialist's memory
            # is private and must not land on the supervisor's thread.
            parent = runtime.config or {}
            specialist = build_specialist(
                spec.bank,
                monthly_profit_rate,
                **(
                    {
                        "web_research_enabled": True,
                        "web_research_required": web_research_required,
                    }
                    if web_search_enabled
                    else {}
                ),
            )
            specialist_context: AgentContext = {"session_id": session_id}
            if web_search_enabled:
                specialist_context["web_search_enabled"] = True
            specialist_config = {
                **parent,
                "configurable": {
                    **(parent.get("configurable") or {}),
                    "thread_id": specialist_thread_id(session_id, spec.bank),
                },
            }
            try:
                previous = specialist.get_state(specialist_config)
                previous_count = len((previous.values or {}).get("messages") or [])
            except (AttributeError, TypeError):
                # Lightweight test doubles and brand-new graphs may not expose
                # readable state. Their result contains only this invocation.
                previous_count = 0
            result = specialist.invoke(
                {"messages": [("user", request)]},
                config=specialist_config,
                context=specialist_context,
            )
            result_messages = result.get("messages") or []
            turn_messages = result_messages[previous_count:]
            tools_used = [
                message.name or "unknown"
                for message in turn_messages
                if isinstance(message, ToolMessage)
            ]
            if web_research_required and "search_bank_web" not in tools_used:
                logger.warning(
                    "bank_specialist corrective_retry bank=%s missing_tool=search_bank_web",
                    spec.bank,
                )
                retry_count = len(result_messages)
                result = specialist.invoke(
                    {"messages": [("user", (
                        "Your delegated request explicitly requires web research, but "
                        "your previous response did not call search_bank_web. Call "
                        "search_bank_web now, report its real status/results, and then "
                        "rewrite the answer. Indexed retrieval does not satisfy this."
                    ))]},
                    config=specialist_config,
                    context=specialist_context,
                )
                retried_messages = result.get("messages") or []
                retry_messages = retried_messages[retry_count:]
                turn_messages = [*turn_messages, *retry_messages]
                tools_used = [
                    message.name or "unknown"
                    for message in turn_messages
                    if isinstance(message, ToolMessage)
                ]
            logger.info(
                "bank_specialist completed bank=%s tools_used=%s",
                spec.bank,
                ",".join(tools_used) or "none",
            )
            if web_research_required and "search_bank_web" not in tools_used:
                return (
                    f"{spec.display_name} could not fulfill the mandatory web-search "
                    "requirement after a corrective retry. Do not present its indexed "
                    "information as comprehensive internet research."
                )
            return _final_text(result, evidence_messages=turn_messages)
        except Exception as exc:  # noqa: BLE001 - a single bank must not end the supervisor turn
            logger.exception(
                "%s specialist exhausted its model retry window", spec.display_name
            )
            return f"{spec.display_name} live specialist failed ({type(exc).__name__})."

    return StructuredTool.from_function(
        func=delegate,
        name=spec.tool_name,
        description=(
            f"Ask only the {spec.display_name} specialist. Use it for this bank's "
            "live endpoints, indexed publications, and—when the request permits—"
            "current bank-domain web research. Pass every relevant attached row, "
            "retrieved fact, and exact source URL in request. It cannot answer for "
            "other banks and must return its evidence to you. Set "
            "web_research_required=true only for explicit internet/web requests, "
            "every-source requests, or exhaustive online research. All/every banks "
            "means broader bank coverage, not mandatory web research. Prefer live "
            "endpoints for current figures, indexed publications for bank knowledge, "
            "and web research only as an optional supplement when enabled. "
            + (
                "Its financing calculator accepts a customer-supplied monthly profit-rate scenario."
                if "monthly_profit_rate" in bank.finance_input_capabilities
                else "Its financing calculator does not accept a customer-supplied monthly profit rate."
            )
        ),
        args_schema=DelegateInput,
    )


def build_specialist_tools() -> list[BaseTool]:
    from .registry import SPECS
    return [build_specialist_tool(spec) for spec in SPECS]
