from __future__ import annotations

import json

from analysis.evidence_registry import EvidenceRegistry, build_evidence_registry
from analysis.models import StaticAnalysisComparison, StaticAnalysisResult
from context.models import PRContext
from execution.base import TestExecutionComparison, TestExecutionResult

PROMPT_VERSION = "codeguard-review-v1.2.0-evidence-registry"
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
    registry: EvidenceRegistry | None = None,
) -> str:
    changed_files = [item.to_prompt_metadata() for item in context.changed_files]
    registry = registry or build_evidence_registry(
        context, static_result, test_result, static_comparison, test_comparison
    )
    schema = {
        "summary": "string",
        "risk_level": "critical|high|medium|low|none",
        "findings": [
            {
                "category": "string",
                "severity": "critical|high|medium|low|info",
                "title": "string",
                "description": "string",
                "suggestion": "string",
                "confidence": "number from 0 to 1",
                "evidence_ids": ["E001"],
            }
        ],
    }
    changed_file_json = _clip(
        json.dumps(changed_files, ensure_ascii=False), 10_000
    )
    body = _clip(context.body, 4_000)
    repo_instructions = _clip(context.repo_instructions or "None found", 12_000)
    registry_json = json.dumps(registry.prompt_items(), ensure_ascii=False)
    if len(registry_json) > 32_000:
        raise ValueError("Evidence registry exceeds the deterministic prompt budget")
    static_summary = (
        static_comparison.summary() if static_comparison else static_result.summary()
    )
    test_summary = (
        test_comparison.summary() if test_comparison else test_result.summary()
    )
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

EVIDENCE REGISTRY (authoritative facts and selectable IDs):
{registry_json}

STATIC ANALYSIS SUMMARY:
{_clip(json.dumps(static_summary, ensure_ascii=False), 8_000)}

TEST EXECUTION SUMMARY:
{_clip(json.dumps(test_summary, ensure_ascii=False), 8_000)}

INSTRUCTIONS:
Review the change for security, correctness, regression, and maintainability risks.
For each finding, cite one or more evidence_ids listed in the EVIDENCE REGISTRY above.
Never invent an ID. Do not output file_path, line_start, line_end, evidence, or
evidence_sources: CodeGuard resolves those fields from the selected IDs. Do not put
paths, line numbers, rules, or test names inside evidence_ids. The output schema is
strict; additional fields are invalid. If no finding is supported, return [].
Ground every claim in the selected facts. A static finding is evidence, not
automatically a confirmed bug.
Environment/preparation/collection/timeout/execution failures mean verification is
unavailable; they are never test regressions. Only call a test regression introduced
when introduced_test_regressions contains that exact test identifier. A raw
introduced_failures set difference without a BASE pass is not regression proof.
Only call a static issue introduced when its registry classification is introduced.
Otherwise say it was observed on HEAD. Test regression evidence may have no
reliably associated source hunk; its test-level result is still evidence, but do
not claim a source line in prose when the registry has no such location.
Do not claim tests ran when status is skipped or unavailable.
One finding may select multiple IDs when several tests support the same issue.
Do not create separate duplicate findings for one evident root cause. Do not
suggest changing tests merely to accept an observed failure without evidence of
intended behavior.
Return only JSON matching this schema:
{schema_json}
"""
    maximum = context.compression.budget_chars + PROMPT_OVERHEAD_BUDGET
    if len(prompt) > maximum:
        raise ValueError("Prompt overhead exceeded the deterministic context budget")
    return prompt
