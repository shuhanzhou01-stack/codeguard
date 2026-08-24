from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


class GroundTruthFinding(BaseModel):
    category: str
    severity: str
    title: str
    file_path: str
    line_start: int | None = None
    line_end: int | None = None
    match_keywords: list[str] = Field(default_factory=list)


class EvaluationCase(BaseModel):
    case_id: str
    repository: str
    base_sha: str
    head_sha: str
    pr_number: int | None = None
    ground_truth_findings: list[GroundTruthFinding] = Field(default_factory=list)
    expected_files: list[str] = Field(default_factory=list)
    expected_lines: dict[str, list[list[int]]] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    title: str = "Evaluation change"
    body: str = ""
    author: str = "fixture"
    diff: str = ""
    changed_files: list[dict] = Field(default_factory=list)
    static_findings: list[dict] = Field(default_factory=list)
    test_evidence: dict = Field(default_factory=dict)
    expected_test_status: str | None = None
    llm_response: dict | None = None
    ablation_responses: dict[str, dict] = Field(default_factory=dict)
    labelled: bool = True
    dataset_version: str = "dataset-v1"


def load_dataset(path: Path) -> list[EvaluationCase]:
    cases: list[EvaluationCase] = []
    if not path.exists():
        raise FileNotFoundError(path)
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            cases.append(EvaluationCase.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValueError) as error:
            raise ValueError(f"Invalid dataset row {line_number}: {error}") from error
    return cases
