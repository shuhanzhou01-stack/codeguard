from __future__ import annotations

import json
from pathlib import Path

from evaluation.dataset import EvaluationCase, GroundTruthFinding, load_dataset
from evaluation.evaluator import evaluate_cases
from evaluation.metrics import match_findings
from llm.client import LLMProvider
from llm.models import LLMResponse, ReviewFinding


def test_empty_dataset_is_honest(tmp_path):
    dataset = tmp_path / "empty.jsonl"
    dataset.write_text("", encoding="utf-8")
    result = evaluate_cases(load_dataset(dataset))
    assert result.status == "No evaluation cases configured."
    assert result.metrics == {}


def test_fixture_benchmark_metrics_are_computed():
    cases = load_dataset(Path("evaluation/datasets/fixture.jsonl"))
    result = evaluate_cases(cases)
    assert result.status == "completed"
    assert result.metrics["case_count"] == 1
    assert result.metrics["labelled_case_count"] == 1
    assert result.metrics["finding_f1"] == 1.0


def _prediction(**changes):
    values = {
        "category": "security",
        "severity": "high",
        "title": "Unsafe shell command",
        "file_path": "runner.py",
        "line_start": None,
        "line_end": None,
        "description": "User input reaches subprocess shell execution.",
        "evidence": "shell=True is present.",
        "suggestion": "Use a fixed argv list.",
        "confidence": 0.9,
    }
    values.update(changes)
    return ReviewFinding(**values)


def test_keyword_matching_and_one_to_one_diagnostics():
    expected = [
        GroundTruthFinding(
            category="security",
            severity="high",
            title="Command injection",
            file_path="runner.py",
            match_keywords=["shell=true", "subprocess"],
        )
    ]
    result = match_findings(
        [_prediction(), _prediction(title="Second duplicate")], expected
    )
    assert len(result["matched_tp_pairs"]) == 1
    assert result["unmatched_fp_indices"] == [1]
    assert result["unmatched_fn_indices"] == []

    no_keyword = match_findings(
        [_prediction(title="Generic", description="Generic", evidence="Generic")],
        expected,
    )
    assert no_keyword["matched_tp_pairs"] == []


class RecordingProvider(LLMProvider):
    def __init__(self):
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> LLMResponse:
        self.prompts.append(prompt)
        return LLMResponse(
            content=json.dumps(
                {"summary": "No findings", "risk_level": "none", "findings": []}
            ),
            model="recording-real-provider",
            latency_ms=2,
            input_tokens=10,
            output_tokens=2,
            estimated_cost=0.01,
        )


def _provider_case(*, labelled: bool = True) -> EvaluationCase:
    return EvaluationCase(
        case_id="provider-case",
        repository="acme/widget",
        base_sha="base",
        head_sha="head",
        labelled=labelled,
        dataset_version="dataset-v2",
        diff=(
            "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n"
            "@@ -1 +1 @@\n-old = 1\n+new = 1\n"
        ),
        changed_files=[{"filename": "app.py", "status": "modified"}],
        static_findings=[
            {
                "tool": "ruff",
                "file_path": "app.py",
                "line": 1,
                "rule_id": "F841",
                "severity": "medium",
                "message": "unused variable",
            }
        ],
        test_evidence={
            "status": "tests_passed",
            "exit_code": 0,
            "stdout": "1 passed",
            "backend": "fixture",
        },
    )


def test_real_provider_receives_distinct_ablation_prompts_and_metadata():
    provider = RecordingProvider()
    results = [
        evaluate_cases(
            [_provider_case()],
            variant,
            provider=provider,
            provider_name="recording",
            model_name="recording-real-provider",
        )
        for variant in ("diff_only", "diff_static", "full_evidence")
    ]
    assert len(provider.prompts) == 3
    assert len(set(provider.prompts)) == 3
    assert [result.variant for result in results] == [
        "diff_only",
        "diff_static",
        "full_evidence",
    ]
    assert all(result.provider == "recording" for result in results)
    assert all(result.model == "recording-real-provider" for result in results)
    assert all(result.dataset_version == "dataset-v2" for result in results)
    assert all(result.metrics["total_token_usage"] == 12 for result in results)


def test_unlabelled_real_world_case_reports_insufficient_labels():
    result = evaluate_cases([_provider_case(labelled=False)])
    assert result.status == "insufficient_labelled_cases"
    assert result.metrics["labelled_case_count"] == 0
    assert result.metrics["finding_f1"] is None
