"""Chat model factory.

    from llm import get_llm

    get_llm().invoke("Merhaba")           # DEFAULT_MODEL
    get_llm("extractor")                  # role, resolved from settings
    get_llm("gemma", temperature=0.7)     # model key
"""

import logging

from langchain_core.language_models.chat_models import BaseChatModel

from config.settings import settings

from .providers import MODELS, ModelSpec, get_provider

logger = logging.getLogger(__name__)

# Role names accepted by get_llm, mapped to the settings field holding the
# model key. Lets callers ask for a job rather than a specific model.
ROLES = {
    "default": "DEFAULT_MODEL",
    "chat": "CHAT_MODEL",
    "extractor": "EXTRACTOR_MODEL",
    "reasoner": "REASONER_MODEL",
}


def resolve_model_key(name: str | None) -> str:
    """Turn a role name or model key into a model key."""
    if name is None:
        name = "default"
    if name in ROLES:
        return getattr(settings, ROLES[name])
    return name


def get_llm(model: str | None = None, **kwargs) -> BaseChatModel:
    """Build a chat model.

    Args:
        model: A model key ("gemma", "qwen", "gpt") or a role name
            ("default", "chat", "extractor", "reasoner"). Defaults to the
            "default" role.
        **kwargs: Passed to the provider. Recognised beyond ChatOpenAI's own
            arguments: `thinking=True` keeps chain-of-thought enabled on
            models that reason by default.

    Returns:
        A LangChain chat model, ready for chains, bind_tools and LangGraph.

    Raises:
        ValueError: if the model is unknown, or max_tokens is below the
            model's usable minimum.
    """
    model_key = resolve_model_key(model)
    provider = get_provider(model_key)
    logger.debug("Creating %s via %s", model_key, provider.provider_name)
    return provider.create(model_key, **kwargs)


def list_models() -> dict[str, ModelSpec]:
    """Every known model and its measured capabilities."""
    return dict(MODELS)
