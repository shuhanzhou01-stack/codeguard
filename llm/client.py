from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod

import requests

from config import Settings
from llm.models import LLMResponse
from security.redaction import sanitize_exception


def _post(session: requests.Session, url: str, **kwargs) -> requests.Response:
    try:
        response = session.post(url, **kwargs)
        response.raise_for_status()
        return response
    except requests.RequestException as error:
        raise RuntimeError(sanitize_exception(error)) from None


class LLMProvider(ABC):
    @abstractmethod
    def complete(self, prompt: str) -> LLMResponse:
        raise NotImplementedError


class FakeLLMProvider(LLMProvider):
    def __init__(self, response: dict | str | None = None) -> None:
        self.response = response or {
            "summary": "Fake provider completed an evidence review; no findings emitted.",
            "risk_level": "none",
            "findings": [],
        }

    def complete(self, prompt: str) -> LLMResponse:
        del prompt
        content = (
            self.response
            if isinstance(self.response, str)
            else json.dumps(self.response)
        )
        return LLMResponse(
            content=content,
            model="codeguard-fake-v1",
            latency_ms=0,
            input_tokens=0,
            output_tokens=0,
            estimated_cost=0.0,
        )


class OpenAICompatibleProvider(LLMProvider):
    """Small provider for OpenAI and OpenAI-compatible APIs."""

    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        if not settings.llm_api_key and settings.llm_provider != "ollama":
            raise RuntimeError("LLM_API_KEY or OPENAI_API_KEY is not set")
        self.settings = settings
        self.session = session or requests.Session()

    def complete(self, prompt: str) -> LLMResponse:
        started = time.perf_counter()
        headers = {"Content-Type": "application/json"}
        if self.settings.llm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"
        response = _post(
            self.session,
            f"{self.settings.llm_api_base}/chat/completions",
            headers=headers,
            json={
                "model": self.settings.llm_model,
                "temperature": self.settings.llm_temperature,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "system",
                        "content": "Return only a JSON object matching the requested schema.",
                    },
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=300 if self.settings.llm_provider == "ollama" else 120,
        )
        payload = response.json()
        choice = payload["choices"][0]
        usage = payload.get("usage") or {}
        return LLMResponse(
            content=choice["message"]["content"],
            model=payload.get("model") or self.settings.llm_model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )


class AnthropicProvider(LLMProvider):
    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        if not settings.llm_api_key:
            raise RuntimeError("LLM_API_KEY or ANTHROPIC_API_KEY is not set")
        self.settings = settings
        self.session = session or requests.Session()

    def complete(self, prompt: str) -> LLMResponse:
        started = time.perf_counter()
        response = _post(
            self.session,
            f"{self.settings.llm_api_base}/v1/messages",
            headers={
                "x-api-key": self.settings.llm_api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.settings.llm_model,
                "max_tokens": 4096,
                "temperature": self.settings.llm_temperature,
                "system": "Return only a JSON object matching the requested schema.",
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=120,
        )
        payload = response.json()
        usage = payload.get("usage") or {}
        content = "".join(
            block.get("text", "")
            for block in payload.get("content", [])
            if block.get("type") == "text"
        )
        return LLMResponse(
            content=content,
            model=payload.get("model") or self.settings.llm_model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )


class GeminiProvider(LLMProvider):
    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        if not settings.llm_api_key:
            raise RuntimeError("LLM_API_KEY or GEMINI_API_KEY is not set")
        self.settings = settings
        self.session = session or requests.Session()

    def complete(self, prompt: str) -> LLMResponse:
        started = time.perf_counter()
        response = _post(
            self.session,
            f"{self.settings.llm_api_base}/models/{self.settings.llm_model}:generateContent",
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.settings.llm_api_key,
            },
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": self.settings.llm_temperature,
                    "responseMimeType": "application/json",
                },
            },
            timeout=120,
        )
        payload = response.json()
        usage = payload.get("usageMetadata") or {}
        parts = payload["candidates"][0]["content"]["parts"]
        return LLMResponse(
            content="".join(part.get("text", "") for part in parts),
            model=self.settings.llm_model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
        )


def create_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "fake":
        return FakeLLMProvider()
    if settings.llm_provider in {"openai", "openai-compatible", "openrouter", "ollama"}:
        return OpenAICompatibleProvider(settings)
    if settings.llm_provider == "anthropic":
        return AnthropicProvider(settings)
    if settings.llm_provider == "gemini":
        return GeminiProvider(settings)
    raise ValueError(f"Unsupported LLM_PROVIDER: {settings.llm_provider}")
