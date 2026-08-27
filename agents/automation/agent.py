"""One-shot structured agent that turns free text into a storable automation.

The same shape as `agents/table_metadata`: a fresh agent per call so a rotated
Gemma tunnel is never pinned, no tools, and a `ToolStrategy` response format so
the result is validated before anything is written.

The validation is the point. A supervisor tool call comes with a schema the
model is asked to fill; this path starts from a sentence typed into a box, and
without a validated structured output the alternative is parsing prose for an
hour of the day -- which is how a report ends up arriving at 09:00 tomorrow
because a model wrote "9 AM" and something read the 9 out of "2029".
"""

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy

from llm import get_llm

from ..shared.clock import now_block

from .models import AutomationDraft
from .prompt import NAME


def build_automation_agent():
    """Build a fresh agent so a rotated Gemma tunnel is never pinned here.

    The clock is not optional on this one. This agent's entire job is turning
    time words into three integers, and half the words it is given are relative:
    "her sabah", "yarın akşam", "hafta sonu", "iş günleri". Without today's date
    and weekday it was resolving those against whatever day its training data
    made it believe it was -- and a schedule read from the wrong week is a
    standing order that fires on the wrong days, silently, every week.
    """
    return create_agent(
        model=get_llm("chat", disable_streaming=True),
        tools=[],
        system_prompt=NAME + now_block(),
        response_format=ToolStrategy(AutomationDraft),
        name="automation_draft",
    )


def draft_automation(text: str) -> AutomationDraft:
    """Read one description and return the automation it asks for.

    Raises `ValueError` on empty input and `RuntimeError` when the model
    returned nothing validatable -- both of which the router turns into a status
    the user can act on. Deliberately not swallowed into a default automation:
    a standing order nobody meant, firing every morning, is worse than a failed
    save the user can retry.
    """
    described = (text or "").strip()
    if not described:
        raise ValueError("An automation needs a description.")
    result = build_automation_agent().invoke(
        {"messages": [("user", described)]}
    )
    structured = result.get("structured_response")
    if not isinstance(structured, AutomationDraft):
        raise RuntimeError("The automation agent returned no validated result.")
    if structured.kind == "needs_clarification":
        raise ValueError(structured.clarification)
    return structured
