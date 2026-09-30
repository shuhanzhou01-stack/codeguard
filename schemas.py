from datetime import datetime

from pydantic import BaseModel


class ChangedFileResponse(BaseModel):
    filename: str
    status: str
    additions: int
    deletions: int


class PullRequestResponse(BaseModel):
    repository: str
    number: int
    title: str
    author: str
    state: str
    analysis_status: str
    changed_files: list[ChangedFileResponse]

class AnalysisRequestResponse(BaseModel):
    repository: str
    pr_number: int
    analysis_run_id: int
    analysis_status: str
    message: str

class AnalysisRunResponse(BaseModel):
    analysis_run_id: int
    pull_request_id: int
    status: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    failed_stage: str | None = None
    error_summary: str | None = None
    base_sha: str | None = None
    head_sha: str | None = None
    trigger_source: str


class ReviewFindingResponse(BaseModel):
    category: str
    severity: str
    title: str
    file_path: str | None
    line_start: int | None
    line_end: int | None
    description: str
    evidence: str
    evidence_ids: list[str]
    resolved_evidence: list[dict]
    suggestion: str
    confidence: float
    fingerprint: str
    grounding_status: str
    grounding_notes: str


class ReviewReportResponse(BaseModel):
    analysis_run_id: int
    summary: str
    risk_level: str
    model: str
    prompt_version: str
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    estimated_cost: float | None
    test_summary: dict
    static_summary: dict
    evidence_registry: list[dict]
    publish_status: str
    grounding_summary: dict[str, int]
    findings: list[ReviewFindingResponse]
    created_at: datetime


class WebhookResponse(BaseModel):
    status: str
    analysis_run_id: int | None = None
