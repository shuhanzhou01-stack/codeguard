from __future__ import annotations

import json
import logging
from typing import Annotated

import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from sqlalchemy.orm import Session

from config import get_settings
from database import get_db
from github_client import get_pull_request, get_pull_request_files
from github_webhook import verify_webhook_signature
from schemas import (
    AnalysisRequestResponse,
    AnalysisRunResponse,
    PullRequestResponse,
    ReviewReportResponse,
    WebhookResponse,
)
from security.redaction import sanitize_exception
from services import (
    claim_webhook_delivery,
    finish_webhook_delivery,
    get_analysis_run,
    get_review_report,
    get_webhook_delivery,
    list_pull_request_analysis_runs,
    request_pull_request_analysis,
    save_pull_request,
)
from tasks import run_analysis

DbSession = Annotated[Session, Depends(get_db)]
SignatureHeader = Annotated[str | None, Header()]

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(
    title="CodeGuard API",
    description="AI-powered GitHub Pull Request verification platform",
    version="1.1.1",
)


@app.get("/")
def root() -> dict[str, str]:
    return {"name": "CodeGuard", "status": "running", "version": "1.1.1"}


@app.get(
    "/pull-requests/{owner}/{repo}/{pr_number}",
    response_model=PullRequestResponse,
)
def read_pull_request(
    owner: str,
    repo: str,
    pr_number: int,
    db: DbSession,
) -> dict:
    try:
        pr_data = get_pull_request(owner, repo, pr_number)
        files_data = get_pull_request_files(owner, repo, pr_number)
    except RuntimeError as error:
        raise HTTPException(
            status_code=503, detail=sanitize_exception(error)
        ) from None
    except requests.HTTPError as error:
        if error.response is not None and error.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Pull request not found") from error
        raise HTTPException(status_code=502, detail="GitHub API request failed") from error
    except requests.RequestException as error:
        raise HTTPException(status_code=503, detail="GitHub API is unavailable") from error

    stored_pr = save_pull_request(db=db, repository=f"{owner}/{repo}", pr_data=pr_data)
    return {
        "repository": f"{owner}/{repo}",
        "number": pr_data["number"],
        "title": pr_data["title"],
        "author": pr_data["user"]["login"],
        "state": pr_data["state"],
        "analysis_status": stored_pr.analysis_status,
        "changed_files": [
            {
                "filename": item["filename"],
                "status": item["status"],
                "additions": item["additions"],
                "deletions": item["deletions"],
            }
            for item in files_data
        ],
    }


@app.post(
    "/pull-requests/{owner}/{repo}/{pr_number}/analyze",
    response_model=AnalysisRequestResponse,
    status_code=202,
)
def request_analysis(
    owner: str,
    repo: str,
    pr_number: int,
    db: DbSession,
) -> dict:
    repository = f"{owner}/{repo}"
    try:
        pr_data = get_pull_request(owner, repo, pr_number)
    except RuntimeError as error:
        raise HTTPException(
            status_code=503, detail=sanitize_exception(error)
        ) from None
    except requests.HTTPError as error:
        if error.response is not None and error.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Pull request not found") from None
        raise HTTPException(status_code=502, detail="GitHub API request failed") from None
    except requests.RequestException:
        raise HTTPException(status_code=503, detail="GitHub API is unavailable") from None

    stored_pr = save_pull_request(db, repository, pr_data)
    base_sha = (pr_data.get("base") or {}).get("sha")
    head_sha = (pr_data.get("head") or {}).get("sha")
    if not base_sha or not head_sha:
        raise HTTPException(status_code=502, detail="GitHub PR is missing commit SHAs")
    analysis_run = request_pull_request_analysis(
        db,
        repository,
        stored_pr.pr_number,
        base_sha=base_sha,
        head_sha=head_sha,
        trigger_source="manual_api",
    )
    if analysis_run is None:
        raise HTTPException(status_code=404, detail="Pull request not found in CodeGuard")
    run_analysis.delay(analysis_run.id)
    return {
        "repository": repository,
        "pr_number": pr_number,
        "analysis_run_id": analysis_run.id,
        "analysis_status": analysis_run.status,
        "message": "Analysis request accepted",
    }


def _analysis_run_payload(analysis_run: object) -> dict:
    return {
        "analysis_run_id": analysis_run.id,
        "pull_request_id": analysis_run.pull_request_id,
        "status": analysis_run.status,
        "created_at": analysis_run.created_at,
        "started_at": analysis_run.started_at,
        "finished_at": analysis_run.finished_at,
        "failed_stage": analysis_run.failed_stage,
        "error_summary": analysis_run.error_summary,
        "base_sha": analysis_run.base_sha,
        "head_sha": analysis_run.head_sha,
        "trigger_source": analysis_run.trigger_source,
    }


@app.get("/analysis-runs/{analysis_run_id}", response_model=AnalysisRunResponse)
def read_analysis_run(analysis_run_id: int, db: DbSession) -> dict:
    analysis_run = get_analysis_run(db, analysis_run_id)
    if analysis_run is None:
        raise HTTPException(status_code=404, detail="Analysis run not found")
    return _analysis_run_payload(analysis_run)


@app.get(
    "/pull-requests/{owner}/{repo}/{pr_number}/analysis-runs",
    response_model=list[AnalysisRunResponse],
)
def read_pull_request_analysis_runs(
    owner: str, repo: str, pr_number: int, db: DbSession
) -> list[dict]:
    return [
        _analysis_run_payload(item)
        for item in list_pull_request_analysis_runs(db, f"{owner}/{repo}", pr_number)
    ]


@app.get(
    "/analysis-runs/{analysis_run_id}/report",
    response_model=ReviewReportResponse,
)
def read_analysis_report(analysis_run_id: int, db: DbSession) -> dict:
    report = get_review_report(db, analysis_run_id)
    if report is None:
        if get_analysis_run(db, analysis_run_id) is None:
            raise HTTPException(status_code=404, detail="Analysis run not found")
        raise HTTPException(status_code=404, detail="Review report is not available")
    return {
        "analysis_run_id": report.analysis_run_id,
        "summary": report.summary,
        "risk_level": report.risk_level,
        "model": report.model,
        "prompt_version": report.prompt_version,
        "latency_ms": report.latency_ms,
        "input_tokens": report.input_tokens,
        "output_tokens": report.output_tokens,
        "estimated_cost": report.estimated_cost,
        "test_summary": report.test_summary,
        "static_summary": report.static_summary,
        "evidence_registry": report.evidence_registry or [],
        "publish_status": report.publish_status,
        "grounding_summary": {
            "grounded": sum(
                item.grounding_status == "grounded" for item in report.findings
            ),
            "partially_grounded": sum(
                item.grounding_status == "partially_grounded"
                for item in report.findings
            ),
            "ungrounded": sum(
                item.grounding_status == "ungrounded" for item in report.findings
            ),
        },
        "created_at": report.created_at,
        "findings": [
            {
                "category": item.category,
                "severity": item.severity,
                "title": item.title,
                "file_path": item.file_path,
                "line_start": item.line_start,
                "line_end": item.line_end,
                "description": item.description,
                "evidence": item.evidence,
                "evidence_ids": item.evidence_ids or [],
                "resolved_evidence": item.resolved_evidence or [],
                "suggestion": item.suggestion,
                "confidence": item.confidence,
                "fingerprint": item.fingerprint,
                "grounding_status": item.grounding_status,
                "grounding_notes": item.grounding_notes,
            }
            for item in report.findings
        ],
    }


@app.post("/github/webhook", response_model=WebhookResponse, status_code=202)
async def github_webhook(
    request: Request,
    db: DbSession,
    x_hub_signature_256: SignatureHeader = None,
    x_github_event: SignatureHeader = None,
    x_github_delivery: SignatureHeader = None,
) -> dict:
    webhook_secret = settings.github_webhook_secret
    if not webhook_secret:
        raise HTTPException(status_code=503, detail="GitHub webhook is disabled")
    body = await request.body()
    if not verify_webhook_signature(body, x_hub_signature_256, webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    if not x_github_delivery:
        raise HTTPException(status_code=400, detail="Missing X-GitHub-Delivery")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from error

    action = payload.get("action")
    if x_github_event != "pull_request" or action not in {
        "opened",
        "reopened",
        "synchronize",
    }:
        return {"status": "ignored", "analysis_run_id": None}
    if not claim_webhook_delivery(
        db, x_github_delivery, x_github_event or "unknown", action
    ):
        return {"status": "duplicate", "analysis_run_id": None}

    try:
        pr_data = payload["pull_request"]
        repository = payload["repository"]["full_name"]
        stored_pr = save_pull_request(db, repository, pr_data)
        base_sha = (pr_data.get("base") or {}).get("sha")
        head_sha = (pr_data.get("head") or {}).get("sha")
        if not base_sha or not head_sha:
            raise RuntimeError("Webhook pull request is missing commit SHAs")
        delivery = get_webhook_delivery(db, x_github_delivery)
        analysis_run = request_pull_request_analysis(
            db,
            repository,
            stored_pr.pr_number,
            base_sha=base_sha,
            head_sha=head_sha,
            trigger_source="github_webhook",
            webhook_delivery_id=delivery.id if delivery is not None else None,
        )
        if analysis_run is None:
            raise RuntimeError("Unable to create analysis run")
        run_analysis.delay(analysis_run.id)
        finish_webhook_delivery(db, x_github_delivery, "completed")
        return {"status": "accepted", "analysis_run_id": analysis_run.id}
    except Exception as error:
        finish_webhook_delivery(
            db,
            x_github_delivery,
            "failed",
            sanitize_exception(error),
        )
        raise HTTPException(status_code=500, detail="Webhook processing failed") from error
