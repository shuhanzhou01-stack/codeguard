from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from analysis.report_builder import finding_fingerprint
from db_models import (
    AnalysisRunDB,
    PullRequestDB,
    ReviewFindingDB,
    ReviewReportDB,
    WebhookDeliveryDB,
)
from llm.models import ReviewReport
from models import PullRequest
from security.redaction import sanitize_text


def analyze_pull_request(pr: PullRequest) -> str:
    try:
        if pr.pr_number <= 0:
            raise ValueError("PR number must be greater than 0")

        pr.start_analysis()

        message = f"Analyzing PR #{pr.pr_number}: {pr.title}"
        return message

    except ValueError as error:
        return f"Analysis failed: {error}"

def save_pull_request(
    db: Session,
    repository: str,
    pr_data: dict
) -> PullRequestDB:

    statement = select(PullRequestDB).where(
        PullRequestDB.repository == repository,
        PullRequestDB.pr_number == pr_data["number"]
    )

    pull_request = db.scalar(statement)

    if pull_request is None:
        pull_request = PullRequestDB(
            repository=repository,
            pr_number=pr_data["number"],
            title=pr_data["title"],
            author=pr_data["user"]["login"],
            state=pr_data["state"],
            analysis_status="pending"
        )

        db.add(pull_request)

    else:
        pull_request.title = pr_data["title"]
        pull_request.author = pr_data["user"]["login"]
        pull_request.state = pr_data["state"]

    try:
        db.commit()
        db.refresh(pull_request)
    except Exception:
        db.rollback()
        raise

    return pull_request

def request_pull_request_analysis(
    db: Session,
    repository: str,
    pr_number: int,
    *,
    base_sha: str,
    head_sha: str,
    trigger_source: str = "manual_api",
    webhook_delivery_id: int | None = None,
) -> AnalysisRunDB | None:

    statement = select(PullRequestDB).where(
        PullRequestDB.repository == repository,
        PullRequestDB.pr_number == pr_number
    )

    pull_request = db.scalar(statement)

    if pull_request is None:
        return None

    pull_request.analysis_status = "pending"

    analysis_run = AnalysisRunDB(
        pull_request_id=pull_request.id,
        status="pending",
        base_sha=base_sha,
        head_sha=head_sha,
        trigger_source=trigger_source,
        webhook_delivery_id=webhook_delivery_id,
    )

    db.add(analysis_run)

    try:
        db.commit()
        db.refresh(analysis_run)
    except Exception:
        db.rollback()
        raise

    return analysis_run

def get_analysis_run(
    db: Session,
    analysis_run_id: int
) -> AnalysisRunDB | None:
    return db.get(
        AnalysisRunDB,
        analysis_run_id
    )


def claim_analysis_run(db: Session, analysis_run_id: int) -> bool:
    """Atomically transition exactly one pending run to running.

    Failed and completed runs are terminal. A retry is represented by a newly
    requested AnalysisRun, preserving the evidence and failure audit trail.
    """
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(AnalysisRunDB)
        .where(
            AnalysisRunDB.id == analysis_run_id,
            AnalysisRunDB.status == "pending",
        )
        .values(
            status="running",
            started_at=now,
            finished_at=None,
            failed_stage=None,
            error_summary=None,
        )
        .execution_options(synchronize_session=False)
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    return result.rowcount == 1


def list_pull_request_analysis_runs(
    db: Session, repository: str, pr_number: int
) -> list[AnalysisRunDB]:
    statement = (
        select(AnalysisRunDB)
        .join(PullRequestDB, AnalysisRunDB.pull_request_id == PullRequestDB.id)
        .where(
            PullRequestDB.repository == repository,
            PullRequestDB.pr_number == pr_number,
        )
        .order_by(AnalysisRunDB.created_at.desc(), AnalysisRunDB.id.desc())
    )
    return list(db.scalars(statement))


def persist_review_report(
    db: Session,
    analysis_run_id: int,
    report: ReviewReport,
    publish_status: str = "disabled",
) -> ReviewReportDB:
    existing = db.scalar(
        select(ReviewReportDB).where(
            ReviewReportDB.analysis_run_id == analysis_run_id
        )
    )
    if existing is None:
        existing = ReviewReportDB(analysis_run_id=analysis_run_id)
        db.add(existing)
    else:
        existing.findings.clear()

    existing.summary = report.summary
    existing.risk_level = report.risk_level
    existing.raw_model_summary = report.raw_model_summary
    existing.raw_model_risk = report.raw_model_risk
    existing.model = report.model
    existing.prompt_version = report.prompt_version
    existing.latency_ms = report.latency_ms
    existing.input_tokens = report.input_tokens
    existing.output_tokens = report.output_tokens
    existing.estimated_cost = report.estimated_cost
    existing.test_summary = report.test_summary
    existing.static_summary = report.static_summary
    existing.publish_status = publish_status

    fingerprints: set[str] = set()
    for finding in report.findings:
        fingerprint = finding_fingerprint(finding)
        if fingerprint in fingerprints:
            continue
        fingerprints.add(fingerprint)
        existing.findings.append(
            ReviewFindingDB(
                category=finding.category,
                severity=finding.severity,
                title=finding.title,
                file_path=finding.file_path,
                line_start=finding.line_start,
                line_end=finding.line_end,
                description=finding.description,
                evidence=finding.evidence,
                suggestion=finding.suggestion,
                confidence=finding.confidence,
                fingerprint=fingerprint,
                grounding_status=finding.grounding_status,
                grounding_notes=finding.grounding_notes,
            )
        )

    try:
        db.commit()
        db.refresh(existing)
    except Exception:
        db.rollback()
        raise
    return existing


def get_review_report(db: Session, analysis_run_id: int) -> ReviewReportDB | None:
    return db.scalar(
        select(ReviewReportDB).where(
            ReviewReportDB.analysis_run_id == analysis_run_id
        )
    )


def claim_webhook_delivery(
    db: Session, delivery_id: str, event: str, action: str | None
) -> bool:
    existing = db.scalar(
        select(WebhookDeliveryDB).where(
            WebhookDeliveryDB.delivery_id == delivery_id
        )
    )
    if existing is not None:
        return False
    db.add(
        WebhookDeliveryDB(
            delivery_id=delivery_id,
            event=event,
            action=action,
            status="processing",
        )
    )
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def get_webhook_delivery(
    db: Session, delivery_id: str
) -> WebhookDeliveryDB | None:
    return db.scalar(
        select(WebhookDeliveryDB).where(
            WebhookDeliveryDB.delivery_id == delivery_id
        )
    )


def finish_webhook_delivery(
    db: Session,
    delivery_id: str,
    status: str,
    error_summary: str | None = None,
) -> None:
    delivery = db.scalar(
        select(WebhookDeliveryDB).where(
            WebhookDeliveryDB.delivery_id == delivery_id
        )
    )
    if delivery is None:
        return
    delivery.status = status
    delivery.error_summary = (
        sanitize_text(error_summary, max_length=2_000)
        if error_summary is not None
        else None
    )
    delivery.processed_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
