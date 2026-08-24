from __future__ import annotations

import hashlib
import os
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from analysis.models import StaticAnalysisResult
from analysis.review_engine import ReviewEngine
from config import Settings
from context.builder import build_pr_context
from evaluation.dataset import EvaluationCase
from evaluation.metrics import calculate_metrics, match_findings
from execution.base import TestExecutionResult
from llm.client import FakeLLMProvider, LLMProvider, create_llm_provider
from llm.prompts import PROMPT_VERSION, build_review_prompt

Variant = Literal["diff_only", "diff_static", "full_evidence"]


class CaseEvaluationResult(BaseModel):
    case_id: str
    finding_count: int
    analysis_latency_ms: int
    llm_latency_ms: int
    token_usage: int
    estimated_cost: float | None
    prompt_fingerprint: str
    matched_tp_pairs: list[dict] = Field(default_factory=list)
    unmatched_fp: list[dict] = Field(default_factory=list)
    unmatched_fn: list[dict] = Field(default_factory=list)


class EvaluationResult(BaseModel):
    status: str
    variant: str
    provider: str
    model: str
    prompt_version: str = PROMPT_VERSION
    generated_at: str
    dataset_version: str
    metrics: dict = Field(default_factory=dict)
    cases: list[CaseEvaluationResult] = Field(default_factory=list)


def create_evaluation_provider(
    provider_name: str, model: str | None = None
) -> LLMProvider | None:
    provider_name = provider_name.strip().lower()
    if provider_name == "fake":
        return None
    defaults = {
        "anthropic": "https://api.anthropic.com",
        "gemini": "https://generativelanguage.googleapis.com/v1beta",
        "ollama": "http://localhost:11434/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "openai": "https://api.openai.com/v1",
        "openai-compatible": "https://api.openai.com/v1",
    }
    configured_model = model or os.getenv("LLM_MODEL")
    if not configured_model:
        raise ValueError("A real evaluation provider requires --model or LLM_MODEL")
    provider_keys = {
        "anthropic": os.getenv("ANTHROPIC_API_KEY"),
        "gemini": os.getenv("GEMINI_API_KEY"),
        "openrouter": os.getenv("OPENROUTER_API_KEY"),
        "openai": os.getenv("OPENAI_API_KEY"),
        "openai-compatible": os.getenv("LLM_API_KEY"),
    }
    settings = Settings.from_env()
    requested_base = os.getenv("LLM_API_BASE") or defaults.get(provider_name)
    settings = replace(
        settings,
        llm_provider=provider_name,
        llm_model=configured_model,
        llm_api_key=os.getenv("LLM_API_KEY") or provider_keys.get(provider_name),
        llm_api_base=(requested_base or settings.llm_api_base).rstrip("/"),
        llm_temperature=0.0,
    )
    return create_llm_provider(settings)


def evaluate_cases(
    cases: list[EvaluationCase],
    variant: Variant = "full_evidence",
    *,
    provider: LLMProvider | None = None,
    provider_name: str = "fake",
    model_name: str | None = None,
) -> EvaluationResult:
    generated_at = datetime.now(timezone.utc).isoformat()
    versions = sorted({case.dataset_version for case in cases})
    dataset_version = ",".join(versions) if versions else "unspecified"
    if not cases:
        return EvaluationResult(
            status="No evaluation cases configured.",
            variant=variant,
            provider=provider_name,
            model=model_name or "codeguard-fake-v1",
            generated_at=generated_at,
            dataset_version=dataset_version,
        )

    metric_rows = []
    case_results: list[CaseEvaluationResult] = []
    observed_model = model_name or "codeguard-fake-v1"
    for case in cases:
        started = time.perf_counter()
        context = build_pr_context(
            repository=case.repository,
            pr_number=case.pr_number or 0,
            pr_data={
                "title": case.title,
                "body": case.body,
                "user": {"login": case.author},
                "base": {"sha": case.base_sha},
                "head": {"sha": case.head_sha},
            },
            changed_files=case.changed_files,
            diff=case.diff,
        )
        static_result = StaticAnalysisResult(
            findings=case.static_findings if variant != "diff_only" else []
        )
        evidence = case.test_evidence if variant == "full_evidence" else {}
        test_result = TestExecutionResult(
            status=evidence.get("status", "skipped"),
            exit_code=evidence.get("exit_code"),
            timed_out=evidence.get("timed_out", False),
            duration_ms=evidence.get("duration_ms", 0),
            stdout=evidence.get("stdout", ""),
            stderr=evidence.get("stderr", ""),
            backend=evidence.get("backend", "evaluation"),
        )
        prompt = build_review_prompt(
            context,
            static_result,
            test_result,
            evidence_variant=variant,
        )
        prompt_fingerprint = hashlib.sha256(prompt.encode()).hexdigest()
        if provider is None:
            scripted = case.ablation_responses.get(variant) or case.llm_response
            case_provider: LLMProvider = FakeLLMProvider(scripted)
        else:
            case_provider = provider
        report = ReviewEngine(case_provider).review(
            context,
            static_result,
            test_result,
            evidence_variant=variant,
        )
        observed_model = report.model
        analysis_latency = int((time.perf_counter() - started) * 1000)
        token_usage = (report.input_tokens or 0) + (report.output_tokens or 0)
        telemetry = {
            "test_status": test_result.status,
            "analysis_latency_ms": analysis_latency,
            "llm_latency_ms": report.latency_ms,
            "token_usage": token_usage,
            "estimated_cost": report.estimated_cost,
        }
        metric_rows.append((case, report.findings, telemetry))
        matching = (
            match_findings(report.findings, case.ground_truth_findings)
            if case.labelled
            else {
                "matched_tp_pairs": [],
                "unmatched_fp_indices": [],
                "unmatched_fn_indices": [],
            }
        )
        case_results.append(
            CaseEvaluationResult(
                case_id=case.case_id,
                finding_count=len(report.findings),
                analysis_latency_ms=analysis_latency,
                llm_latency_ms=report.latency_ms,
                token_usage=token_usage,
                estimated_cost=report.estimated_cost,
                prompt_fingerprint=prompt_fingerprint,
                matched_tp_pairs=matching["matched_tp_pairs"],
                unmatched_fp=[
                    report.findings[index].model_dump()
                    for index in matching["unmatched_fp_indices"]
                ],
                unmatched_fn=[
                    case.ground_truth_findings[index].model_dump()
                    for index in matching["unmatched_fn_indices"]
                ],
            )
        )
    metrics = calculate_metrics(metric_rows)
    return EvaluationResult(
        status=(
            "completed"
            if metrics["labelled_case_count"]
            else "insufficient_labelled_cases"
        ),
        variant=variant,
        provider=provider_name,
        model=observed_model,
        generated_at=generated_at,
        dataset_version=dataset_version,
        metrics=metrics,
        cases=case_results,
    )
