"""The chat models the user can pick between.

One GET, no arguments, because the frontend needs this list before it can draw
the composer's Advanced menu and has nothing to filter it by.

The list is derived from `MODELS`, which records behaviour *measured* against
the running vLLM host rather than copied from a model card. Serving it means the
picker gains a model the moment the host does, instead of when someone remembers
to edit a TypeScript constant.
"""

import logging

from fastapi import APIRouter

from config.settings import settings
from llm.factory import resolve_model_key
from llm.providers.vllm_provider import MODELS

from ..schemas.models import ModelOut, ModelsResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/models", tags=["models"])


@router.get("", response_model=ModelsResponse)
def list_models() -> ModelsResponse:
    """Every selectable model, with the facts the picker needs to describe it."""
    return ModelsResponse(
        models=[
            ModelOut(
                key=key,
                model_id=spec.model_id,
                notes=spec.notes,
                context_window=spec.context_window,
                # Its own fact, not `thinking_by_default`. Gemma does not reason
                # unless asked and still honours the flag; reading the default as
                # the capability is what hid that, and left the switch disabled
                # for the model most people use.
                supports_thinking=spec.supports_thinking,
            )
            for key, spec in MODELS.items()
        ],
        # What `get_llm("chat")` resolves to today, so the menu can mark the row
        # that answers when the user has chosen nothing.
        default=resolve_model_key(settings.CHAT_MODEL),
    )
