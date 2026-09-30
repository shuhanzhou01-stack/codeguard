from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Severity = Literal["critical", "high", "medium", "low", "info"]
RiskLevel = Literal["critical", "high", "medium", "low", "none"]
GroundingStatus = Literal["grounded", "partially_grounded", "ungrounded"]


class EvidenceReference(BaseModel):
    source: Literal["diff", "static", "test", "static_delta", "test_delta"]
    identifier: str | None = None


class ReviewFinding(BaseModel):
    category: str
    severity: Severity
    title: str
    file_path: str | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    description: str
    evidence: str
    suggestion: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_sources: list[EvidenceReference] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    resolved_evidence: list[dict] = Field(default_factory=list)
    grounding_status: GroundingStatus = "ungrounded"
    grounding_notes: str = "Grounding validation has not run."

    @model_validator(mode="after")
    def validate_line_range(self) -> ReviewFinding:
        if self.line_end is not None and self.line_start is None:
            raise ValueError("line_start is required when line_end is provided")
        if (
            self.line_start is not None
            and self.line_end is not None
            and self.line_end < self.line_start
        ):
            raise ValueError("line_end cannot be before line_start")
        return self


class ReviewReport(BaseModel):
    summary: str
    risk_level: RiskLevel
    findings: list[ReviewFinding] = Field(default_factory=list)
    evidence_registry: list[dict] = Field(default_factory=list)
    test_summary: dict = Field(default_factory=dict)
    static_summary: dict = Field(default_factory=dict)
    model: str
    latency_ms: int = 0
    prompt_version: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost: float | None = None
    raw_model_summary: str | None = None
    raw_model_risk: RiskLevel | None = None

    def grounding_summary(self) -> dict[str, int]:
        counts = {
            "grounded": 0,
            "partially_grounded": 0,
            "ungrounded": 0,
        }
        for finding in self.findings:
            counts[finding.grounding_status] += 1
        return counts


class LLMFindingProposal(BaseModel):
    """Model-owned judgment only; evidence metadata is resolved by CodeGuard."""

    model_config = ConfigDict(extra="forbid")

    category: str
    severity: Severity
    title: str
    description: str
    suggestion: str = ""
    confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    evidence_ids: list[str]


class LLMReviewProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    risk_level: RiskLevel
    findings: list[LLMFindingProposal] = Field(default_factory=list)


class LLMResponse(BaseModel):
    content: str
    model: str
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost: float | None = None
