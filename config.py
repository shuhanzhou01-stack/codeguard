from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv

load_dotenv()


def _as_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(name: str, default: int, minimum: int = 1) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return max(minimum, int(value))
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


def _as_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error


@dataclass(frozen=True)
class Settings:
    github_token: str | None
    github_webhook_secret: str | None
    redis_url: str
    llm_provider: str
    llm_model: str
    llm_api_key: str | None
    llm_api_base: str
    llm_temperature: float
    publish_comments: bool
    enable_static_analysis: bool
    enable_tests: bool
    max_diff_size: int
    test_timeout_seconds: int
    test_memory_limit: str
    test_pid_limit: int
    log_level: str

    @classmethod
    def from_env(cls) -> Settings:
        provider = os.getenv("LLM_PROVIDER", "fake").strip().lower()
        provider_keys = {
            "anthropic": os.getenv("ANTHROPIC_API_KEY"),
            "gemini": os.getenv("GEMINI_API_KEY"),
            "openrouter": os.getenv("OPENROUTER_API_KEY"),
        }
        default_bases = {
            "anthropic": "https://api.anthropic.com",
            "gemini": "https://generativelanguage.googleapis.com/v1beta",
            "ollama": "http://localhost:11434/v1",
            "openrouter": "https://openrouter.ai/api/v1",
        }
        return cls(
            github_token=os.getenv("GITHUB_TOKEN"),
            github_webhook_secret=os.getenv("GITHUB_WEBHOOK_SECRET"),
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            llm_provider=provider,
            llm_model=os.getenv("LLM_MODEL", "codeguard-fake-v1"),
            llm_api_key=(
                os.getenv("LLM_API_KEY")
                or provider_keys.get(provider)
                or os.getenv("OPENAI_API_KEY")
            ),
            llm_api_base=os.getenv(
                "LLM_API_BASE",
                default_bases.get(provider, "https://api.openai.com/v1"),
            ).rstrip("/"),
            llm_temperature=_as_float("LLM_TEMPERATURE", 0.0),
            publish_comments=_as_bool("CODEGUARD_PUBLISH_COMMENTS", False),
            enable_static_analysis=_as_bool(
                "CODEGUARD_ENABLE_STATIC_ANALYSIS", True
            ),
            enable_tests=_as_bool("CODEGUARD_ENABLE_TESTS", True),
            max_diff_size=_as_int("CODEGUARD_MAX_DIFF_SIZE", 120_000),
            test_timeout_seconds=_as_int(
                "CODEGUARD_TEST_TIMEOUT_SECONDS", 120
            ),
            test_memory_limit=os.getenv(
                "CODEGUARD_TEST_MEMORY_LIMIT", "512m"
            ),
            test_pid_limit=_as_int("CODEGUARD_TEST_PID_LIMIT", 128),
            log_level=os.getenv("CODEGUARD_LOG_LEVEL", "INFO").upper(),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
