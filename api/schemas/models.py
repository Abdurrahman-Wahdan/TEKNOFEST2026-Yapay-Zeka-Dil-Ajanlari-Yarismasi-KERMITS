"""The chat models this deployment can answer with, on the wire.

The UI needs three things to draw a model picker: what to call each model, what
it is good at, and whether the thinking switch means anything for it. All three
already exist as measured facts in `llm.providers.vllm_provider.MODELS`; this
module is the envelope that carries them to the browser.

Served rather than hardcoded in the frontend for the same reason the context
window is read from vLLM rather than pinned in a constant: the served models
change, and a list duplicated in TypeScript is a list that silently goes stale.
"""

from pydantic import BaseModel, Field


class ModelOut(BaseModel):
    """One model the user can pick."""

    key: str = Field(description='The value to send back on AskRequest.model: "gemma" | "qwen" | "gpt".')
    model_id: str = Field(description="What vLLM serves it as, e.g. google/gemma-4-31B-it.")
    notes: str = Field(description="Measured behaviour, shown as the row's one-line description.")
    context_window: int = Field(
        description="Tokens the model accepts. Currently the value measured into "
        "MODELS; it becomes the number vLLM reports once /v1/models is probed."
    )
    supports_thinking: bool = Field(
        description="Whether the thinking switch does anything for this model. "
        "False for models that ignore enable_thinking, or whose output is "
        "unusable with it on -- the UI disables the switch and says why rather "
        "than offering a toggle that changes nothing."
    )


class ModelsResponse(BaseModel):
    """Every selectable model, plus which one answers when none is chosen."""

    models: list[ModelOut]
    default: str = Field(description="The key used when AskRequest.model is null.")
