from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import replace

from fastapi.testclient import TestClient

import main
from database import get_db
from db_models import AnalysisRunDB
from llm.models import ReviewFinding, ReviewReport
from services import (
    persist_review_report,
    request_pull_request_analysis,
    save_pull_request,
)


def test_analyze_endpoint_remains_202(session_factory, monkeypatch):
    db = session_factory()
    save_pull_request(
        db,
        "acme/widget",
        {
            "number": 9,
            "title": "Test",
            "user": {"login": "dev"},
            "state": "open",
        },
    )
    db.close()

    def override_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    queued: list[int] = []
    monkeypatch.setattr(
        main,
        "get_pull_request",
        lambda owner, repo, number: {
            "number": number,
            "title": "Pinned",
            "user": {"login": "dev"},
            "state": "open",
            "base": {"sha": "base-a"},
            "head": {"sha": "head-a"},
        },
    )
    monkeypatch.setattr(main.run_analysis, "delay", lambda run_id: queued.append(run_id))
    main.app.dependency_overrides[get_db] = override_db
    try:
        response = TestClient(main.app).post("/pull-requests/acme/widget/9/analyze")
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 202
    assert response.json()["analysis_status"] == "pending"
    assert queued == [response.json()["analysis_run_id"]]
    check = session_factory()
    try:
        run = check.get(AnalysisRunDB, response.json()["analysis_run_id"])
        assert run.base_sha == "base-a"
        assert run.head_sha == "head-a"
        assert run.trigger_source == "manual_api"
    finally:
        check.close()


def test_report_endpoint_returns_structured_evidence(session_factory):
    db = session_factory()
    save_pull_request(
        db,
        "acme/widget",
        {
            "number": 10,
            "title": "Report",
            "user": {"login": "dev"},
            "state": "open",
        },
    )
    run = request_pull_request_analysis(
        db, "acme/widget", 10, base_sha="base", head_sha="head"
    )
    assert run is not None
    report = ReviewReport(
        summary="Verified",
        risk_level="low",
        findings=[
            ReviewFinding(
                category="quality",
                severity="low",
                title="Example",
                description="Description",
                evidence="Evidence",
                suggestion="Suggestion",
                confidence=0.8,
                grounding_status="grounded",
                grounding_notes="Test fixture.",
            )
        ],
        test_summary={"status": "passed"},
        static_summary={"finding_count": 0},
        model="fake",
        prompt_version="v1",
    )
    persist_review_report(db, run.id, report)
    run_id = run.id
    db.close()

    def override_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    main.app.dependency_overrides[get_db] = override_db
    try:
        response = TestClient(main.app).get(f"/analysis-runs/{run_id}/report")
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["test_summary"]["status"] == "passed"
    assert len(response.json()["findings"][0]["fingerprint"]) == 64
    assert response.json()["grounding_summary"]["grounded"] == 1


def test_webhook_pins_payload_shas_and_links_delivery(session_factory, monkeypatch):
    def override_db():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    secret = "webhook-test-secret"
    payload = {
        "action": "synchronize",
        "repository": {"full_name": "acme/widget"},
        "pull_request": {
            "number": 12,
            "title": "Webhook",
            "user": {"login": "dev"},
            "state": "open",
            "base": {"sha": "webhook-base"},
            "head": {"sha": "webhook-head"},
        },
    }
    body = json.dumps(payload, separators=(",", ":")).encode()
    signature = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    queued: list[int] = []
    monkeypatch.setattr(main, "settings", replace(main.settings, github_webhook_secret=secret))
    monkeypatch.setattr(main.run_analysis, "delay", lambda run_id: queued.append(run_id))
    main.app.dependency_overrides[get_db] = override_db
    try:
        response = TestClient(main.app).post(
            "/github/webhook",
            content=body,
            headers={
                "content-type": "application/json",
                "x-hub-signature-256": signature,
                "x-github-event": "pull_request",
                "x-github-delivery": "delivery-12",
            },
        )
    finally:
        main.app.dependency_overrides.clear()
    assert response.status_code == 202
    assert queued == [response.json()["analysis_run_id"]]
    db = session_factory()
    try:
        run = db.get(AnalysisRunDB, queued[0])
        assert run.base_sha == "webhook-base"
        assert run.head_sha == "webhook-head"
        assert run.trigger_source == "github_webhook"
        assert run.webhook_delivery_id is not None
    finally:
        db.close()
