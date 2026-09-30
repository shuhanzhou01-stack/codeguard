from __future__ import annotations

import pytest
from pydantic import ValidationError

from analysis.evidence_registry import build_evidence_registry
from analysis.evidence_resolution import resolve_finding, resolve_findings
from analysis.grounding import FindingGroundingValidator
from analysis.models import StaticAnalysisResult, StaticFinding, compare_static_results
from analysis.report_builder import finding_fingerprint
from analysis.review_engine import ReviewEngine
from context.builder import build_pr_context
from execution.base import TestExecutionResult as ExecutionResult
from execution.base import compare_test_results
from llm.client import FakeLLMProvider
from llm.models import LLMFindingProposal

DIFF = """diff --git a/src/ops.py b/src/ops.py
index 1111111..2222222 100644
--- a/src/ops.py
+++ b/src/ops.py
@@ -1,2 +1,2 @@
 def adjust(value):
-    return value
+    return value + 1
"""
TEST_A = "tests.test_ops::test_adjust_one"
TEST_B = "tests.test_ops::test_adjust_two"


def _inputs(tmp_path, *, traceable: bool = True):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "ops.py").write_text(
        "def adjust(value):\n    return value + 1\n", encoding="utf-8"
    )
    test_source = (
        "from src.ops import adjust\n"
        "def test_adjust_one(): assert adjust(1) == 1\n"
        "def test_adjust_two(): assert adjust(2) == 2\n"
        if traceable
        else "def test_adjust_one(): assert helper(1) == 1\n"
        "def test_adjust_two(): assert helper(2) == 2\n"
    )
    (tmp_path / "tests" / "test_ops.py").write_text(
        test_source, encoding="utf-8"
    )
    context = build_pr_context(
        repository="sample/operations",
        pr_number=3,
        pr_data={
            "title": "Change adjustment",
            "user": {"login": "author"},
            "base": {"sha": "base"},
            "head": {"sha": "head"},
        },
        changed_files=[{"filename": "src/ops.py", "status": "modified"}],
        diff=DIFF,
        repo_path=tmp_path,
    )
    static = StaticAnalysisResult(
        findings=[
            StaticFinding(
                tool="lint",
                file_path="src/ops.py",
                line=2,
                rule_id="L123",
                severity="high",
                message="Changed return expression requires review.",
            )
        ]
    )
    static_comparison = compare_static_results(StaticAnalysisResult(), static)
    base = ExecutionResult(
        status="tests_passed",
        backend="fixture",
        passed_tests=[TEST_A, TEST_B],
    )
    head = ExecutionResult(
        status="tests_failed",
        backend="fixture",
        failed_tests=[TEST_A, TEST_B],
    )
    test_comparison = compare_test_results(base, head)
    registry = build_evidence_registry(
        context, static, head, static_comparison, test_comparison
    )
    return context, static, head, static_comparison, test_comparison, registry


def _proposal(ids, **changes):
    values = {
        "category": "regression",
        "severity": "medium",
        "title": "Adjustment changes expected result",
        "description": "The changed function causes a BASE-pass-to-HEAD-fail test.",
        "suggestion": "Restore the intended calculation.",
        "confidence": 0.8,
        "evidence_ids": ids,
    }
    values.update(changes)
    return LLMFindingProposal(**values)


def _resolve(proposal, inputs):
    context, static, head, static_comparison, test_comparison, registry = inputs
    return resolve_finding(
        proposal,
        registry,
        context,
        static,
        head,
        static_comparison,
        test_comparison,
        FindingGroundingValidator(),
    )


def test_registry_ids_are_stable_and_include_deterministic_static_regression_hunk(
    tmp_path,
):
    inputs = _inputs(tmp_path)
    context, static, head, static_comparison, test_comparison, registry = inputs
    assert [item.id for item in registry.items] == ["E001", "E002", "E003", "E004"]
    assert [item.type for item in registry.items] == [
        "diff_hunk", "static", "test_regression", "test_regression"
    ]
    assert registry.get("E002").rule == "L123"
    assert registry.get("E002").classification == "introduced"
    assert registry.get("E003").base_result == "passed"
    assert registry.get("E003").head_result == "failed"
    assert registry.get("E003").related_diff_hunk == "E001"
    assert registry.get("E003").file_path == "src/ops.py"
    assert registry.get("E003").line_start == 2
    assert registry.get("E003").test_file_path == "tests/test_ops.py"
    assert registry.model_dump() == build_evidence_registry(
        context, static, head, static_comparison, test_comparison
    ).model_dump()


def test_valid_ids_resolve_static_location_and_tool_severity(tmp_path):
    inputs = _inputs(tmp_path)
    finding = _resolve(
        _proposal(["E002", "E001"], category="security", severity="low"), inputs
    )
    assert finding.grounding_status == "grounded"
    assert (finding.file_path, finding.line_start) == ("src/ops.py", 2)
    assert finding.severity == "high"
    assert finding.evidence_ids == ["E002", "E001"]
    assert finding.resolved_evidence[0]["rule"] == "L123"
    assert "L123" in finding.evidence


def test_nonexistent_id_fails_grounding_even_when_another_id_is_valid(tmp_path):
    inputs = _inputs(tmp_path)
    finding = _resolve(_proposal(["E003", "E999"]), inputs)
    assert finding.grounding_status == "ungrounded"
    assert finding.file_path is None
    assert finding.line_start is None
    assert "E999" in finding.grounding_notes


def test_multiple_regression_ids_support_one_source_finding(tmp_path):
    inputs = _inputs(tmp_path)
    finding = _resolve(_proposal(["E003", "E004"]), inputs)
    assert finding.grounding_status == "grounded"
    assert (finding.file_path, finding.line_start) == ("src/ops.py", 2)
    assert all(test in finding.evidence for test in (TEST_A, TEST_B))
    assert finding.file_path != "tests/test_ops.py"


def test_unknown_source_location_keeps_test_level_evidence(tmp_path):
    inputs = _inputs(tmp_path, traceable=False)
    registry = inputs[-1]
    assert registry.get("E003").file_path is None
    finding = _resolve(_proposal(["E003"]), inputs)
    assert finding.grounding_status == "grounded"
    assert finding.file_path is None
    assert finding.line_start is None
    assert finding.resolved_evidence[0]["test_file_path"] == "tests/test_ops.py"
    assert finding.resolved_evidence[0].get("related_diff_hunk") is None


def test_duplicate_regressions_merge_only_with_same_known_source_hunk(tmp_path):
    inputs = _inputs(tmp_path)
    proposals = [_proposal(["E003"]), _proposal(["E004"], title="Second failure")]
    findings = resolve_findings(
        proposals, inputs[-1], *inputs[:5], FindingGroundingValidator()
    )
    assert len(findings) == 1
    assert findings[0].evidence_ids == ["E003", "E004"]
    assert findings[0].grounding_status == "grounded"


def test_unrelated_test_level_regressions_are_not_forced_together(tmp_path):
    inputs = _inputs(tmp_path, traceable=False)
    proposals = [_proposal(["E003"]), _proposal(["E004"], title="Other failure")]
    findings = resolve_findings(
        proposals, inputs[-1], *inputs[:5], FindingGroundingValidator()
    )
    assert len(findings) == 2
    assert all(item.file_path is None for item in findings)
    assert finding_fingerprint(findings[0]) != finding_fingerprint(findings[1])


def test_model_schema_rejects_free_form_location_and_evidence():
    with pytest.raises(ValidationError):
        _proposal(
            ["E001"],
            file_path="invented.py",
            line_start=99,
            evidence="Model-authored citation",
        )


def test_clean_review_retains_prompt_visible_registry(tmp_path):
    context, _, head, _, _, registry = _inputs(tmp_path)
    report = ReviewEngine(FakeLLMProvider()).review(
        context, StaticAnalysisResult(), head
    )
    assert report.findings == []
    assert report.evidence_registry
    assert report.evidence_registry[0]["id"] == registry.items[0].id
