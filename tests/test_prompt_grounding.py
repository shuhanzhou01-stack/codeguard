from __future__ import annotations

import pytest

from analysis.grounding import FindingGroundingValidator
from analysis.models import (
    StaticAnalysisResult,
    StaticFinding,
    compare_static_results,
)
from analysis.review_engine import ReviewEngine
from context.builder import build_pr_context
from execution.base import TestExecutionResult as ExecutionResult
from execution.base import compare_test_results
from llm.client import FakeLLMProvider
from llm.models import ReviewFinding
from llm.prompts import PROMPT_OVERHEAD_BUDGET, build_review_prompt

DIFF = """diff --git a/app.py b/app.py
index 1111111..2222222 100644
--- a/app.py
+++ b/app.py
@@ -1,3 +1,5 @@
 value = 1
+checked = True
+result = value
 return value
"""


def _context(*, patch: str = "PATCH_SENTINEL", budget: int = 120_000):
    return build_pr_context(
        repository="acme/widget",
        pr_number=1,
        pr_data={
            "title": "Validate",
            "body": "",
            "user": {"login": "dev"},
            "base": {"sha": "base"},
            "head": {"sha": "head"},
        },
        changed_files=[
            {
                "filename": "app.py",
                "status": "modified",
                "additions": 2,
                "deletions": 0,
                "patch": patch,
            }
        ],
        diff=DIFF,
        context_budget=budget,
    )


def _finding(**changes) -> ReviewFinding:
    values = {
        "category": "correctness",
        "severity": "medium",
        "title": "Changed behavior",
        "file_path": "app.py",
        "line_start": 3,
        "line_end": 3,
        "description": "The changed line affects the result.",
        "evidence": "The diff adds result = value.",
        "suggestion": "Add a focused assertion.",
        "confidence": 0.8,
        "evidence_sources": [{"source": "diff", "identifier": "app.py:3"}],
    }
    values.update(changes)
    return ReviewFinding(**values)


def _tests(status: str = "tests_passed") -> ExecutionResult:
    return ExecutionResult(status=status, backend="fixture", stdout="1 passed")


def test_prompt_does_not_duplicate_changed_file_patch():
    context = _context()
    prompt = build_review_prompt(context, StaticAnalysisResult(), _tests())
    assert "PATCH_SENTINEL" not in prompt
    assert '"filename": "app.py"' in prompt
    assert prompt.count("+result = value") == 1


def test_prompt_respects_diff_plus_overhead_budget():
    large_diff = DIFF + ("+large_context_line\n" * 10_000)
    context = build_pr_context(
        repository="acme/widget",
        pr_number=1,
        pr_data={
            "title": "Large",
            "user": {"login": "dev"},
            "base": {"sha": "base"},
            "head": {"sha": "head"},
        },
        changed_files=[
            {
                "filename": "app.py",
                "status": "modified",
                "patch": "UNCOMPRESSED_PATCH_MUST_NOT_APPEAR",
            }
        ],
        diff=large_diff,
        context_budget=2_000,
    )
    prompt = build_review_prompt(context, StaticAnalysisResult(), _tests())
    assert len(prompt) <= context.compression.budget_chars + PROMPT_OVERHEAD_BUDGET
    assert "UNCOMPRESSED_PATCH_MUST_NOT_APPEAR" not in prompt


@pytest.mark.parametrize(
    "unsafe_path", ["/etc/passwd", "../app.py", "C:\\Windows\\secret.txt"]
)
def test_grounding_rejects_absolute_and_traversal_paths(unsafe_path):
    validated = FindingGroundingValidator().validate_finding(
        _finding(file_path=unsafe_path), _context(), StaticAnalysisResult(), _tests()
    )
    assert validated.grounding_status == "ungrounded"
    assert validated.file_path is None
    assert validated.line_start is None


def test_grounding_rejects_hallucinated_file():
    validated = FindingGroundingValidator().validate_finding(
        _finding(file_path="missing.py"), _context(), StaticAnalysisResult(), _tests()
    )
    assert validated.grounding_status == "ungrounded"
    assert validated.file_path is None


def test_grounding_accepts_valid_file_and_hunk_line():
    validated = FindingGroundingValidator().validate_finding(
        _finding(), _context(), StaticAnalysisResult(), _tests()
    )
    assert validated.grounding_status == "grounded"
    assert validated.file_path == "app.py"
    assert validated.line_start == 3


def test_grounding_clears_impossible_line_without_failing_finding():
    validated = FindingGroundingValidator().validate_finding(
        _finding(line_start=999, line_end=999),
        _context(),
        StaticAnalysisResult(),
        _tests(),
    )
    assert validated.grounding_status == "partially_grounded"
    assert validated.line_start is None
    assert validated.line_end is None


def test_grounding_validates_static_and_test_evidence_references():
    static = StaticAnalysisResult(
        findings=[
            StaticFinding(
                tool="bandit",
                file_path="app.py",
                line=3,
                rule_id="B602",
                severity="high",
                message="shell=True",
            )
        ]
    )
    validator = FindingGroundingValidator()
    valid = validator.validate_finding(
        _finding(
            evidence="Bandit recorded B602.",
            evidence_sources=[{"source": "static", "identifier": "B602"}],
        ),
        _context(),
        static,
        _tests(),
    )
    invalid_static = validator.validate_finding(
        _finding(
            evidence="Bandit recorded B999.",
            evidence_sources=[{"source": "static", "identifier": "B999"}],
        ),
        _context(),
        static,
        _tests(),
    )
    invalid_test = validator.validate_finding(
        _finding(
            evidence="Pytest proves test_missing.",
            evidence_sources=[{"source": "test", "identifier": "test_missing"}],
        ),
        _context(),
        static,
        _tests(),
    )
    assert valid.grounding_status == "grounded"
    assert invalid_static.grounding_status == "ungrounded"
    assert invalid_test.grounding_status == "ungrounded"


def test_bad_grounding_does_not_abort_other_findings(monkeypatch):
    validator = FindingGroundingValidator()
    original = validator.validate_finding

    def flaky(finding, *args):
        if finding.title == "bad":
            raise RuntimeError("validator failure")
        return original(finding, *args)

    monkeypatch.setattr(validator, "validate_finding", flaky)
    findings = validator.validate_findings(
        [_finding(title="bad"), _finding(title="good")],
        _context(),
        StaticAnalysisResult(),
        _tests(),
    )
    assert [item.grounding_status for item in findings] == [
        "ungrounded",
        "grounded",
    ]


def test_introduced_static_claim_requires_static_delta_evidence():
    finding = StaticFinding(
        tool="bandit",
        file_path="app.py",
        line=3,
        rule_id="B602",
        severity="high",
        message="shell=True",
    )
    head = StaticAnalysisResult(findings=[finding])
    comparison = compare_static_results(StaticAnalysisResult(), head)
    validator = FindingGroundingValidator()
    head_only = validator.validate_finding(
        _finding(
            title="Introduced static issue",
            evidence="Bandit recorded B602 as introduced.",
            evidence_sources=[{"source": "static", "identifier": "B602"}],
        ),
        _context(),
        head,
        _tests(),
        comparison,
    )
    delta_backed = validator.validate_finding(
        _finding(
            title="Introduced static issue",
            evidence="Bandit recorded B602 as introduced.",
            evidence_sources=[{"source": "static_delta", "identifier": "B602"}],
        ),
        _context(),
        head,
        _tests(),
        comparison,
    )
    assert head_only.grounding_status == "partially_grounded"
    assert delta_backed.grounding_status == "grounded"


def test_introduced_test_claim_requires_exact_confirmed_test_delta():
    node_id = "tests/test_payment.py::test_payment"
    base = ExecutionResult(
        status="tests_passed", backend="fixture", passed_tests=[node_id]
    )
    head = ExecutionResult(
        status="tests_failed", backend="fixture", failed_tests=[node_id]
    )
    comparison = compare_test_results(base, head)
    validated = FindingGroundingValidator().validate_finding(
        _finding(
            title="Introduced test regression",
            evidence="Pytest records the test failed on HEAD after passing on BASE.",
            evidence_sources=[{"source": "test_delta", "identifier": node_id}],
        ),
        _context(),
        StaticAnalysisResult(),
        head,
        test_comparison=comparison,
    )
    assert validated.grounding_status == "grounded"


def test_fake_diff_identifier_is_ungrounded():
    validated = FindingGroundingValidator().validate_finding(
        _finding(evidence_sources=[{"source": "diff", "identifier": "fake.py:3"}]),
        _context(),
        StaticAnalysisResult(),
        _tests(),
    )
    assert validated.grounding_status == "ungrounded"


def test_empty_evidence_sources_are_at_most_partially_grounded():
    validated = FindingGroundingValidator().validate_finding(
        _finding(evidence_sources=[]),
        _context(),
        StaticAnalysisResult(),
        _tests(),
    )
    assert validated.grounding_status == "partially_grounded"


def test_review_engine_recalculates_hallucinated_critical_report():
    response = {
        "summary": "Critical model warning",
        "risk_level": "critical",
        "findings": [
            {
                "category": "security",
                "severity": "critical",
                "title": "Hallucinated critical",
                "file_path": "missing.py",
                "line_start": 1,
                "line_end": 1,
                "description": "Not in the repository.",
                "evidence": "Invented evidence.",
                "suggestion": "None.",
                "confidence": 0.99,
                "evidence_sources": [
                    {"source": "diff", "identifier": "missing.py:1"}
                ],
            }
        ],
    }
    report = ReviewEngine(FakeLLMProvider(response)).review(
        _context(), StaticAnalysisResult(), _tests()
    )
    assert report.raw_model_risk == "critical"
    assert report.findings[0].grounding_status == "ungrounded"
    assert report.risk_level == "none"
    assert report.summary == "No evidence-grounded issues were identified."
