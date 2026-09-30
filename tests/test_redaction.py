from __future__ import annotations

import logging
from dataclasses import replace

import pytest
import requests

from analysis.models import StaticAnalysisResult
from analysis.pipeline import AnalysisPipeline
from analysis.review_engine import ReviewEngine
from config import Settings
from context.builder import build_pr_context
from db_models import AnalysisRunDB
from execution.base import TestExecutionResult as ExecutionResult
from llm.client import FakeLLMProvider, GeminiProvider, OpenAICompatibleProvider
from security.redaction import REDACTED, sanitize_exception, sanitize_text
from services import request_pull_request_analysis, save_pull_request


def test_sanitize_text_removes_headers_query_database_and_process_secret(monkeypatch):
    secret = "process-secret-12345"
    monkeypatch.setenv("GITHUB_TOKEN", secret)
    raw = (
        f"Authorization: Bearer {secret} "
        f"https://api.example.test/x?access_token={secret} "
        f"postgresql://user:{secret}@db/codeguard"
    )
    sanitized = sanitize_text(raw)
    assert secret not in sanitized
    assert sanitized.count(REDACTED) >= 3


class ErrorResponse:
    def raise_for_status(self):
        raise requests.HTTPError(
            "401 for https://provider.test/v1?api_key=provider-secret-67890"
        )


class ErrorSession:
    def post(self, *args, **kwargs):
        return ErrorResponse()


def test_provider_http_exception_does_not_expose_secret(monkeypatch):
    secret = "provider-secret-67890"
    monkeypatch.setenv("LLM_API_KEY", secret)
    settings = replace(
        Settings.from_env(),
        llm_provider="openai",
        llm_api_key=secret,
        llm_api_base="https://provider.test/v1",
    )
    with pytest.raises(RuntimeError) as captured:
        OpenAICompatibleProvider(settings, session=ErrorSession()).complete("prompt")
    assert secret not in str(captured.value)


class GeminiResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {
            "candidates": [{"content": {"parts": [{"text": "{}"}]}}],
            "usageMetadata": {},
        }


class RecordingSession:
    def __init__(self):
        self.kwargs = None

    def post(self, *args, **kwargs):
        self.kwargs = kwargs
        return GeminiResponse()


class OpenAICompatibleResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": "{}"}}], "usage": {}}


class RecordingOpenAISession:
    def __init__(self):
        self.kwargs = None

    def post(self, *args, **kwargs):
        self.kwargs = kwargs
        return OpenAICompatibleResponse()


@pytest.mark.parametrize(
    ("provider", "expected_timeout"),
    [("ollama", 300), ("openai", 120)],
)
def test_local_ollama_has_longer_read_timeout(provider, expected_timeout):
    session = RecordingOpenAISession()
    settings = replace(
        Settings.from_env(),
        llm_provider=provider,
        llm_model="test-model",
        llm_api_key=None if provider == "ollama" else "test-key",
        llm_api_base="http://provider.test/v1",
    )
    OpenAICompatibleProvider(settings, session=session).complete("prompt")
    assert session.kwargs["timeout"] == expected_timeout


def test_gemini_key_is_sent_in_header_not_query():
    session = RecordingSession()
    settings = replace(
        Settings.from_env(),
        llm_provider="gemini",
        llm_model="gemini-test",
        llm_api_key="gemini-secret-12345",
        llm_api_base="https://gemini.test/v1beta",
    )
    GeminiProvider(settings, session=session).complete("prompt")
    assert "params" not in session.kwargs
    assert session.kwargs["headers"]["x-goog-api-key"] == "gemini-secret-12345"


class FailingGitHub:
    def __init__(self, secret: str) -> None:
        self.secret = secret

    def get_compare(self, *args):
        raise RuntimeError(f"Authorization: Bearer {self.secret}")


def test_pipeline_persisted_error_and_log_are_redacted(
    session_factory, monkeypatch, caplog
):
    secret = "github-secret-24680"
    monkeypatch.setenv("GITHUB_TOKEN", secret)
    db = session_factory()
    save_pull_request(
        db,
        "acme/widget",
        {
            "number": 3,
            "title": "Redact",
            "user": {"login": "dev"},
            "state": "open",
        },
    )
    run = request_pull_request_analysis(
        db, "acme/widget", 3, base_sha="base", head_sha="head"
    )
    assert run is not None
    run_id = run.id
    db.close()
    settings = replace(
        Settings.from_env(),
        publish_comments=False,
        enable_static_analysis=False,
        enable_tests=False,
        llm_provider="fake",
    )
    caplog.set_level(logging.INFO)
    with pytest.raises(RuntimeError) as captured:
        AnalysisPipeline(
            session_factory=session_factory,
            settings=settings,
            github_client=FailingGitHub(secret),
        ).run(run_id)
    db = session_factory()
    try:
        stored = db.get(AnalysisRunDB, run_id)
        assert stored.status == "failed"
        assert secret not in stored.error_summary
    finally:
        db.close()
    assert secret not in str(captured.value)
    assert secret not in caplog.text


def test_sanitize_exception_is_bounded():
    output = sanitize_exception(RuntimeError("x" * 10_000), max_length=100)
    assert len(output) == 100


def test_llm_prompt_redacts_secret_from_changed_code(monkeypatch):
    secret = "github_pat_ABCDEFGHIJKLMNOPQRSTUVWX1234567890"
    monkeypatch.setenv("GITHUB_TOKEN", secret)
    diff = (
        "diff --git a/app.py b/app.py\n"
        "@@ -0,0 +1 @@\n"
        f"+TOKEN = '{secret}'\n"
    )
    context = build_pr_context(
        repository="acme/widget",
        pr_number=1,
        pr_data={
            "title": "Add token",
            "body": "",
            "user": {"login": "dev"},
            "base": {"sha": "base"},
            "head": {"sha": "head"},
        },
        changed_files=[{"filename": "app.py", "status": "added", "additions": 1}],
        diff=diff,
    )

    class RecordingProvider(FakeLLMProvider):
        prompt = None

        def complete(self, prompt):
            self.prompt = prompt
            return super().complete(prompt)

    provider = RecordingProvider()
    ReviewEngine(provider).review(
        context,
        StaticAnalysisResult(),
        ExecutionResult(status="skipped", backend="fixture"),
    )
    assert secret not in provider.prompt
    assert REDACTED in provider.prompt


def test_private_key_block_is_redacted():
    private_key = "-----BEGIN PRIVATE KEY-----\nsecret-data\n-----END PRIVATE KEY-----"
    assert private_key not in sanitize_text(private_key)
