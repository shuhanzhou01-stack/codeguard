from __future__ import annotations

import json

from pydantic import ValidationError

from analysis.grounding import FindingGroundingValidator
from analysis.models import StaticAnalysisComparison, StaticAnalysisResult
from analysis.report_builder import recalculate_verified_report
from context.models import PRContext
from execution.base import TestExecutionComparison, TestExecutionResult
from llm.client import LLMProvider
from llm.models import ReviewReport
from llm.prompts import PROMPT_VERSION, build_review_prompt


def parse_structured_review(content: str) -> dict:
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines)
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        raise ValueError("LLM response was not valid JSON") from None
    if not isinstance(payload, dict):
        raise TypeError("LLM response must be a JSON object")
    return payload


class ReviewEngine:
    def __init__(
        self,
        provider: LLMProvider,
        grounding_validator: FindingGroundingValidator | None = None,
    ) -> None:
        self.provider = provider
        self.grounding_validator = grounding_validator or FindingGroundingValidator()

    def review(
        self,
        context: PRContext,
        static_result: StaticAnalysisResult,
        test_result: TestExecutionResult,
        static_comparison: StaticAnalysisComparison | None = None,
        test_comparison: TestExecutionComparison | None = None,
        evidence_variant: str = "full_evidence",
    ) -> ReviewReport:
        prompt = build_review_prompt(
            context,
            static_result,
            test_result,
            static_comparison,
            test_comparison,
            evidence_variant,
        )
        response = self.provider.complete(prompt)
        payload = parse_structured_review(response.content)
        payload.update(
            {
                "test_summary": (
                    test_comparison.summary()
                    if test_comparison is not None
                    else test_result.summary()
                ),
                "static_summary": (
                    static_comparison.summary()
                    if static_comparison is not None
                    else static_result.summary()
                ),
                "model": response.model,
                "latency_ms": response.latency_ms,
                "prompt_version": PROMPT_VERSION,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "estimated_cost": response.estimated_cost,
            }
        )
        try:
            report = ReviewReport.model_validate(payload)
        except ValidationError:
            raise ValueError("LLM response did not match review schema") from None
        report.raw_model_summary = report.summary
        report.raw_model_risk = report.risk_level
        report.findings = self.grounding_validator.validate_findings(
            report.findings,
            context,
            static_result,
            test_result,
            static_comparison,
            test_comparison,
        )
        return recalculate_verified_report(report)
