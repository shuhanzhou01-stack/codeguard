from __future__ import annotations

import pytest
from pydantic import ValidationError

from analysis.report_builder import (
    COMMENT_MARKER,
    build_review_comment,
    finding_fingerprint,
    recalculate_verified_report,
)
from analysis.review_engine import parse_structured_review
from llm.models import ReviewFinding, ReviewReport


def finding(**changes) -> ReviewFinding:
    values = {
        "category": "security",
        "severity": "high",
        "title": "Unsafe shell",
        "file_path": "runner.py",
        "line_start": 4,
        "line_end": 4,
        "description": "User input reaches a shell.",
        "evidence": "Diff adds shell=True.",
        "suggestion": "Use argv and shell=False.",
        "confidence": 0.9,
        "grounding_status": "grounded",
        "grounding_notes": "Test fixture.",
    }
    values.update(changes)
    return ReviewFinding(**values)


def test_review_finding_schema_rejects_invalid_range():
    with pytest.raises(ValidationError):
        finding(line_start=5, line_end=4)


def test_structured_response_parser_accepts_json_fence():
    assert parse_structured_review("```json\n{\"risk_level\": \"none\"}\n```")[
        "risk_level"
    ] == "none"


def test_fingerprint_is_stable_and_report_comment_has_marker():
    item = finding()
    assert finding_fingerprint(item) == finding_fingerprint(item.model_copy())
    report = ReviewReport(
        summary="One issue",
        risk_level="high",
        findings=[item],
        test_summary={"status": "passed"},
        static_summary={"finding_count": 1},
        model="fake",
        prompt_version="v1",
    )
    comment = build_review_comment(report)
    assert comment.startswith(COMMENT_MARKER)
    assert "runner.py:4" in comment


def test_comment_excludes_ungrounded_but_keeps_partial_findings():
    report = ReviewReport(
        summary="Grounding filter",
        risk_level="medium",
        findings=[
            finding(
                title="Partial issue",
                grounding_status="partially_grounded",
            ),
            finding(
                title="Hallucinated issue",
                file_path=None,
                line_start=None,
                line_end=None,
                grounding_status="ungrounded",
            ),
        ],
        model="fake",
        prompt_version="v1.1",
    )
    comment = build_review_comment(report)
    assert "Partial issue" in comment
    assert "Hallucinated issue" not in comment
    assert "1 partial, 1 ungrounded" in comment


def test_verified_report_drops_critical_risk_from_only_ungrounded_finding():
    report = recalculate_verified_report(
        ReviewReport(
            summary="Critical model warning",
            risk_level="critical",
            raw_model_summary="Critical model warning",
            raw_model_risk="critical",
            findings=[
                finding(
                    severity="critical",
                    grounding_status="ungrounded",
                    file_path=None,
                    line_start=None,
                    line_end=None,
                )
            ],
            model="fake",
            prompt_version="v1.1.1",
        )
    )
    assert report.risk_level == "none"
    assert report.summary == "No evidence-grounded issues were identified."
    assert report.raw_model_risk == "critical"
    assert "CRITICAL" not in build_review_comment(report)


def test_verified_report_uses_highest_publishable_severity_only():
    report = recalculate_verified_report(
        ReviewReport(
            summary="Raw critical",
            risk_level="critical",
            findings=[
                finding(severity="high", grounding_status="grounded"),
                finding(
                    title="Unsupported critical",
                    severity="critical",
                    grounding_status="ungrounded",
                    file_path=None,
                    line_start=None,
                    line_end=None,
                ),
            ],
            model="fake",
            prompt_version="v1.1.1",
        )
    )
    assert report.risk_level == "high"
