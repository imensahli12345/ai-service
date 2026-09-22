"""Application configuration read from the environment."""

from functools import lru_cache
import os

from dotenv import load_dotenv


# Loading this explicitly also makes `.env` work when uvicorn is started locally.
load_dotenv()


def _optional(name: str) -> str | None:
    return os.getenv(name) or None


def _boolean(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "on"}


class Settings:
    """Small dependency-free settings object, populated from `.env` and OS env."""

    def __init__(self) -> None:
        self.ai_service_api_key = _optional("AI_SERVICE_API_KEY")
        self.openrouter_api_key = _optional("OPENROUTER_API_KEY")
        self.openrouter_base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        self.openrouter_model = os.getenv(
            "OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"
        )
        self.openai_timeout_seconds = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "7.0"))
        if self.openai_timeout_seconds <= 0:
            raise ValueError("OPENAI_TIMEOUT_SECONDS must be greater than zero")
        self.enable_keyword_fallback = _boolean("ENABLE_KEYWORD_FALLBACK", True)
        self.demo_fail_hook = _boolean("DEMO_FAIL_HOOK", True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
