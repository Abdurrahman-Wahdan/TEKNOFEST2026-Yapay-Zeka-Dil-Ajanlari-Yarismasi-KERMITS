"""Application settings, loaded from environment variables and .env.

One flat Settings class. Grouping is by section banner and field-name prefix,
not by nesting, so any value can be found and changed in one place.
"""

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchor paths to this file, not the working directory, so settings load the
# same way from pytest, a CLI run, or a server process.
PROJECT_ROOT = Path(__file__).parent.parent
ENV_FILE = PROJECT_ROOT / ".env"

# Model keys served by the local vLLM host. Roles below must point at one of
# these; the validator enforces it.
MODEL_KEYS = ("gemma", "qwen", "gpt")


class Settings(BaseSettings):
    """Settings for the local LLM, embedding and vector store stack."""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ===== LLM (local vLLM) =====
    VLLM_BASE_URL: str = "https://unbundle-semisoft-mouth.ngrok-free.dev"
    VLLM_API_KEY: str = Field(
        default="EMPTY",
        description="vLLM needs no auth, but the OpenAI client rejects an empty key.",
    )
    LLM_TIMEOUT: float = Field(default=300.0, gt=0)
    LLM_MAX_RETRIES: int = Field(default=1, ge=0)
    LLM_TEMPERATURE: float = Field(
        default=0.0,
        description="Extraction favours repeatability over variety.",
    )

    # Role -> model key. Lets model choice change in .env without touching code.
    DEFAULT_MODEL: str = "qwen"
    CHAT_MODEL: str = Field(default="gemma", description="Fastest, cleanest Turkish.")
    EXTRACTOR_MODEL: str = Field(default="qwen", description="Best structured output.")
    REASONER_MODEL: str = "gpt"

    # ===== Embeddings =====
    EMBEDDING_PROVIDER: str = "local"
    EMBEDDING_MODEL: str = Field(
        default="BAAI/bge-m3",
        description="Multilingual, strong on Turkish, Apache-2.0. Change freely.",
    )
    EMBEDDING_DEVICE: str = "cpu"
    EMBEDDING_DIMENSIONS: int = Field(
        default=1024,
        gt=0,
        description="Must match the model. Collections are created with this size.",
    )
    EMBEDDING_BATCH_SIZE: int = Field(default=16, gt=0)

    # ===== Vector store (local Qdrant) =====
    VECTOR_STORE: str = "qdrant"
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_TIMEOUT: int = Field(default=30, gt=0)
    QDRANT_COLLECTION_CAMPAIGNS: str = "campaigns"

    # ===== Banks (live calculator endpoints) =====
    BANK_HTTP_TIMEOUT: float = Field(default=30.0, gt=0)
    BANK_HTTP_RETRIES: int = Field(
        default=1,
        ge=0,
        description="Extra attempts. Kuveyt Türk's finance endpoint "
        "intermittently answers 200 with an empty Meta.",
    )
    BANK_USER_AGENT: str = Field(
        default="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36",
        description="These are public browser endpoints; they expect a browser.",
    )

    # ===== Application =====
    LOG_LEVEL: str = "INFO"
    ENVIRONMENT: str = "development"

    @model_validator(mode="after")
    def validate_model_roles(self):
        """Fail at startup if a role points at a model that does not exist.

        Without this a typo in .env surfaces mid-run as a confusing provider
        error, long after the process started.
        """
        roles = {
            "DEFAULT_MODEL": self.DEFAULT_MODEL,
            "CHAT_MODEL": self.CHAT_MODEL,
            "EXTRACTOR_MODEL": self.EXTRACTOR_MODEL,
            "REASONER_MODEL": self.REASONER_MODEL,
        }
        for field, value in roles.items():
            if value not in MODEL_KEYS:
                raise ValueError(
                    f"{field}={value!r} is not a known model. "
                    f"Valid keys: {', '.join(MODEL_KEYS)}"
                )
        return self


settings = Settings()
