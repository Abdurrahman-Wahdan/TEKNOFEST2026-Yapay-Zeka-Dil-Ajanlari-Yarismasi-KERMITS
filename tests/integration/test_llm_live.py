"""Live checks against the vLLM host.

Needs the three servers up. Skips cleanly when they are not.
"""

import pytest
from pydantic import BaseModel, Field

from llm import get_llm, list_models

pytestmark = [pytest.mark.integration, pytest.mark.slow]

MODEL_KEYS = list(list_models())


@pytest.fixture(scope="module")
def live() -> bool:
    import httpx

    from config.settings import settings

    try:
        url = f"{settings.VLLM_BASE_URL}/qwen/v1/models"
        return httpx.get(url, timeout=10).status_code == 200
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.parametrize("model_key", MODEL_KEYS)
def test_every_model_answers(live, model_key):
    if not live:
        pytest.skip("vLLM host is not reachable")

    reply = get_llm(model_key, max_tokens=400).invoke("2+2 kaç eder?")
    assert reply.content.strip(), f"{model_key} returned empty content"
    assert reply.usage_metadata["output_tokens"] > 0


def test_qwen_content_is_clean_with_thinking_off(live):
    """Reasoning must not leak into the answer."""
    if not live:
        pytest.skip("vLLM host is not reachable")

    reply = get_llm("qwen", max_tokens=600).invoke("Kâr payı oranı nedir? Tek cümle.")
    assert "</think>" not in reply.content
    assert len(reply.content) < 600, "answer looks like it contains reasoning"


class Campaign(BaseModel):
    """Optional bank name is the point: absent data must come back as None."""

    bank: str | None = Field(default=None, description="Bank name, null if absent")
    profit_rate: float = Field(description="Monthly profit rate, e.g. 1.89")
    term_months: int = Field(description="Term in months")


def test_extraction_returns_none_instead_of_inventing(live):
    """function_calling is the method we rely on; json_schema fabricates here."""
    if not live:
        pytest.skip("vLLM host is not reachable")

    text = "Yeni ev sahiplerine %1,89 kâr payı ile 120 aya kadar konut finansmanı."
    result = (
        get_llm("extractor", max_tokens=1000)
        .with_structured_output(Campaign, method="function_calling")
        .invoke(text)
    )

    assert result.profit_rate == pytest.approx(1.89)
    assert result.term_months == 120
    assert result.bank is None, f"invented a bank name: {result.bank!r}"
