"""Transient model context for feedback stored outside the chat transcript."""

from langchain.agents.middleware import ModelRequest, ModelResponse, wrap_model_call
from langchain.messages import SystemMessage


@wrap_model_call
def inject_feedback_context(request: ModelRequest, handler) -> ModelResponse:
    """Append current session feedback without checkpointing it as a chat turn."""
    context = request.runtime.context or {}
    notes = context.get("feedback_notes", "")
    if not notes:
        return handler(request)

    system_message = SystemMessage(
        content=[
            *request.system_message.content_blocks,
            {
                "type": "text",
                "text": f"<user_feedback_notes>\n{notes}\n</user_feedback_notes>",
            },
        ]
    )
    return handler(request.override(system_message=system_message))
