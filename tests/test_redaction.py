from __future__ import annotations

import logging
from dataclasses import replace

import pytest
import requests

from analysis.pipeline import AnalysisPipeline
from config import Settings
from db_models import AnalysisRunDB
from llm.client import GeminiProvider, OpenAICompatibleProvider
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
