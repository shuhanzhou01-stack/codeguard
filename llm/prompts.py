from __future__ import annotations

import json

from analysis.models import StaticAnalysisComparison, StaticAnalysisResult
from context.models import PRContext
from execution.base import TestExecutionComparison, TestExecutionResult

PROMPT_VERSION = "codeguard-review-v1.1.1"
PROMPT_OVERHEAD_BUDGET = 64_000


def _clip(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n[CodeGuard truncated this evidence section.]"


def build_review_prompt(
    context: PRContext,
    static_result: StaticAnalysisResult,
    test_result: TestExecutionResult,
    static_comparison: StaticAnalysisComparison | None = None,
    test_comparison: TestExecutionComparison | None = None,
    evidence_variant: str = "full_evidence",
) -> str:
    changed_files = [item.to_prompt_metadata() for item in context.changed_files]
    static_evidence = static_comparison or static_result
    test_evidence = test_comparison or test_result
    schema = {
        "summary": "string",
        "risk_level": "critical|high|medium|low|none",
        "findings": [
            {
                "category": "string",
                "severity": "critical|high|medium|low|info",
                "title": "string",
                "file_path": "string|null",
                "line_start": "integer|null",
                "line_end": "integer|null",
                "description": "string",
                "evidence": "string",
                "suggestion": "string",
                "confidence": "number from 0 to 1",
                "evidence_sources": [
                    {
                        "source": "diff|static|test|static_delta|test_delta",
                        "identifier": "changed path[:line], rule ID/tool, or exact test ID",
                    }
                ],
            }
        ],
    }
    changed_file_json = _clip(
        json.dumps(changed_files, ensure_ascii=False), 10_000
    )
    body = _clip(context.body, 4_000)
    repo_instructions = _clip(context.repo_instructions or "None found", 12_000)
    static_json = _clip(static_evidence.model_dump_json(), 16_000)
    test_json = _clip(test_evidence.model_dump_json(), 10_000)
    schema_json = _clip(json.dumps(schema, ensure_ascii=False), 4_000)
    prompt = f"""FACTS (the only allowed evidence):
Repository: {context.repository}
Pull request: #{context.pr_number} — {context.title}
Evidence variant: {evidence_variant}
Body: {body}
Base SHA: {context.base_sha}
Head SHA: {context.head_sha}
Changed files: {changed_file_json}
Repository instructions: {repo_instructions}
Diff compression metadata: {context.compression.model_dump_json()}

DIFF:
{context.diff}

STATIC ANALYSIS EVIDENCE:
{static_json}

TEST EXECUTION EVIDENCE:
{test_json}

INSTRUCTIONS:
Review the change for security, correctness, regression, and maintainability risks.
Ground every finding in the facts above. Never invent files, line numbers, tests, or
runtime behavior. A static finding is evidence, not automatically a confirmed bug.
Environment/preparation/collection/timeout/execution failures mean verification is
unavailable; they are never test regressions. Only call a test regression introduced
when introduced_test_regressions contains that exact test identifier. A raw
introduced_failures set difference without a BASE pass is not regression proof.
Only call a static issue introduced when static delta evidence explicitly marks the
matching rule/file introduced. Otherwise say it was observed on HEAD.
If a line cannot be mapped from the diff, use null line_start and line_end.
Do not claim tests ran when status is skipped or unavailable.
Use diff identifiers as changed path or changed path:line. Use static_delta/test_delta
for introduced static/test attribution and exact identifiers from the supplied delta.
Every finding must have at least one structured evidence_sources entry.
Return only JSON matching this schema:
{schema_json}
"""
    maximum = context.compression.budget_chars + PROMPT_OVERHEAD_BUDGET
    if len(prompt) > maximum:
        raise ValueError("Prompt overhead exceeded the deterministic context budget")
    return prompt
