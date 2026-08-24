from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


class PullRequestDB(Base):
    __tablename__ = "pull_requests"

    __table_args__ = (
        UniqueConstraint(
            "repository",
            "pr_number",
            name="uq_pull_request_repository_number"
        ),
    )

    id: Mapped[int] = mapped_column(
        primary_key=True
    )

    repository: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    pr_number: Mapped[int] = mapped_column(
        nullable=False
    )

    title: Mapped[str] = mapped_column(
        String(500),
        nullable=False
    )

    author: Mapped[str] = mapped_column(
        String(255),
        nullable=False
    )

    state: Mapped[str] = mapped_column(
        String(50),
        nullable=False
    )

    analysis_status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="pending"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False
    )

class AnalysisRunDB(Base):
    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(
        primary_key=True
    )

    pull_request_id: Mapped[int] = mapped_column(
        ForeignKey("pull_requests.id"),
        nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="pending"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    failed_stage: Mapped[str | None] = mapped_column(String(100), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    head_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trigger_source: Mapped[str] = mapped_column(
        String(50), nullable=False, default="manual_api"
    )
    webhook_delivery_id: Mapped[int | None] = mapped_column(
        ForeignKey("webhook_deliveries.id"), nullable=True
    )

    review_report: Mapped["ReviewReportDB | None"] = relationship(
        back_populates="analysis_run",
        cascade="all, delete-orphan",
        uselist=False,
    )

class TestExecutionDB(Base):
    __tablename__ = "test_executions"

    id: Mapped[int] = mapped_column(
        primary_key=True
    )

    analysis_run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_runs.id"),
        nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="pending"
    )

    exit_code: Mapped[int | None] = mapped_column(
        nullable=True
    )

    stdout: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    stderr: Mapped[str | None] = mapped_column(
        Text,
        nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False
    )

    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    timed_out: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(nullable=True)
    backend: Mapped[str | None] = mapped_column(String(100), nullable=True)
    revision_role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    passed_tests: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    failed_tests: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    error_tests: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    skipped_tests: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class ReviewReportDB(Base):
    __tablename__ = "review_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_run_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_runs.id"), nullable=False, unique=True
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    raw_model_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_model_risk: Mapped[str | None] = mapped_column(String(20), nullable=True)
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    latency_ms: Mapped[int] = mapped_column(nullable=False, default=0)
    input_tokens: Mapped[int | None] = mapped_column(nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(nullable=True)
    estimated_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    test_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    static_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    publish_status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="disabled"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    analysis_run: Mapped[AnalysisRunDB] = relationship(
        back_populates="review_report"
    )
    findings: Mapped[list["ReviewFindingDB"]] = relationship(
        back_populates="review_report",
        cascade="all, delete-orphan",
        order_by="ReviewFindingDB.id",
    )


class ReviewFindingDB(Base):
    __tablename__ = "review_findings"
    __table_args__ = (
        UniqueConstraint(
            "review_report_id", "fingerprint", name="uq_review_finding_fingerprint"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    review_report_id: Mapped[int] = mapped_column(
        ForeignKey("review_reports.id"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    file_path: Mapped[str | None] = mapped_column(String(1_000), nullable=True)
    line_start: Mapped[int | None] = mapped_column(nullable=True)
    line_end: Mapped[int | None] = mapped_column(nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    suggestion: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    grounding_status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="ungrounded"
    )
    grounding_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    review_report: Mapped[ReviewReportDB] = relationship(back_populates="findings")


class WebhookDeliveryDB(Base):
    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True)
    delivery_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    event: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="processing")
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
