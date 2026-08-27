"""Settings load, validate, and stay in sync with .env.example."""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from config.settings import MODEL_KEYS, PROJECT_ROOT, Settings, settings

pytestmark = pytest.mark.unit


def test_defaults_are_usable():
    assert settings.VLLM_BASE_URL.startswith("http")
    assert settings.QDRANT_URL.startswith("http")
    assert settings.EMBEDDING_DIMENSIONS > 0
    assert settings.LLM_TEMPERATURE == 0.0


def test_voice_defaults_use_the_shared_remote_api():
    defaults = Settings(_env_file=None)
    assert defaults.VOICE_PROVIDER == "remote"
    assert defaults.SPEECH_PROVIDER == "remote"
    assert defaults.VOICE_REMOTE_BASE_URL == ""
    assert defaults.VOICE_REMOTE_STT_ROUTE == "/whisper/v1"
    assert defaults.VOICE_REMOTE_TTS_ROUTE == "/tts/v1"
    assert defaults.SPEECH_REMOTE_TIMESTEPS == 16
    assert defaults.SPEECH_TIMESTEPS == 4


def test_voice_provider_typos_are_rejected_at_startup():
    with pytest.raises(ValidationError, match="VOICE_PROVIDER"):
        Settings(VOICE_PROVIDER="remtoe")
    with pytest.raises(ValidationError, match="SPEECH_PROVIDER"):
        Settings(SPEECH_PROVIDER="remtoe")


def test_every_role_points_at_a_real_model():
    for role in ("DEFAULT_MODEL", "CHAT_MODEL", "EXTRACTOR_MODEL", "REASONER_MODEL"):
        assert getattr(settings, role) in MODEL_KEYS


def test_bad_role_is_rejected_at_startup():
    """A typo in .env must fail immediately, not halfway through a run."""
    with pytest.raises(ValidationError, match="EXTRACTOR_MODEL"):
        Settings(EXTRACTOR_MODEL="llama")


def test_positive_bounds_are_enforced():
    with pytest.raises(ValidationError):
        Settings(EMBEDDING_DIMENSIONS=0)
    with pytest.raises(ValidationError):
        Settings(LLM_TIMEOUT=0)


def test_env_example_keys_all_exist():
    """.env.example drifting from Settings is a silent trap: the key looks
    configured but is ignored."""
    example = PROJECT_ROOT / ".env.example"
    assert example.exists(), ".env.example is missing"

    fields = set(Settings.model_fields)
    for line in example.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key = re.split(r"=", line, maxsplit=1)[0].strip()
        assert key in fields, f".env.example sets {key}, which is not a Settings field"


def test_settings_are_anchored_to_the_project_root():
    """Settings must load the same from any working directory."""
    assert (PROJECT_ROOT / "config" / "settings.py").exists()
    assert isinstance(PROJECT_ROOT, Path)
