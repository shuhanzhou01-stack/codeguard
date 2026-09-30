from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, Field

from analysis.models import StaticAnalysisComparison, StaticAnalysisResult
from context.models import PRContext
from execution.base import TestExecutionComparison, TestExecutionResult

EvidenceType = Literal["diff_hunk", "static", "test_regression", "test_observation"]
DIFF_FILE_RE = re.compile(r"^diff --git a/(.+?) b/(.+?)$")
HUNK_RE = re.compile(
    r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@"
)


def _repo_path(value: str) -> str | None:
    value = value.strip().replace("\\", "/")
    if not value or value.startswith("/") or re.match(r"^[A-Za-z]:/", value):
        return None
    path = PurePosixPath(value)
    if ".." in path.parts or path.is_absolute():
        return None
    return path.as_posix().removeprefix("./")


class EvidenceItem(BaseModel):
    id: str
    type: EvidenceType
    source: str
    classification: str
    rule: str | None = None
    file_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    hunk_start: int | None = None
    hunk_end: int | None = None
    test_name: str | None = None
    test_file_path: str | None = None
    base_result: str | None = None
    head_result: str | None = None
    related_diff_hunk: str | None = None
    related_symbol: str | None = None
    severity: str | None = None
    message: str


class EvidenceRegistry(BaseModel):
    items: list[EvidenceItem] = Field(default_factory=list)

    def get(self, evidence_id: str) -> EvidenceItem | None:
        return next((item for item in self.items if item.id == evidence_id), None)

    def prompt_items(self) -> list[dict]:
        return [item.model_dump(exclude_none=True) for item in self.items]


def _diff_hunks(diff: str) -> list[EvidenceItem]:
    hunks: list[EvidenceItem] = []
    path: str | None = None
    start: int | None = None
    count = 0
    new_line = 0
    added: list[int] = []

    def finish() -> None:
        if path is None or start is None:
            return
        first = min(added) if added else (start if count else None)
        last = max(added) if added else (start + count - 1 if count else None)
        hunks.append(
            EvidenceItem(
                id="",
                type="diff_hunk",
                source="git_diff",
                classification="changed",
                file_path=path,
                line_start=first,
                line_end=last,
                hunk_start=start if count else None,
                hunk_end=start + count - 1 if count else None,
                message=f"Changed BASE-to-HEAD diff hunk in {path}.",
            )
        )

    for line in diff.splitlines():
        file_match = DIFF_FILE_RE.match(line)
        if file_match:
            finish()
            path = _repo_path(file_match.group(2))
            start = None
            added = []
            continue
        hunk_match = HUNK_RE.match(line)
        if hunk_match:
            finish()
            start = int(hunk_match.group("start"))
            count = int(hunk_match.group("count") or "1")
            new_line = start
            added = []
            continue
        if start is None:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added.append(new_line)
            new_line += 1
        elif line.startswith(" "):
            new_line += 1
    finish()
    hunks.sort(key=lambda item: (item.file_path or "", item.hunk_start or 0))
    return [
        item.model_copy(update={"id": f"E{index:03d}"})
        for index, item in enumerate(hunks, start=1)
    ]


def _matching_hunk(
    hunks: list[EvidenceItem], file_path: str, line: int | None
) -> EvidenceItem | None:
    if line is None:
        return None
    matches = [
        item
        for item in hunks
        if item.file_path == file_path
        and item.hunk_start is not None
        and item.hunk_start <= line <= (item.hunk_end or item.hunk_start)
    ]
    return matches[0] if len(matches) == 1 else None


def _safe_file(root: Path, relative: str) -> Path | None:
    normalized = _repo_path(relative)
    if normalized is None:
        return None
    candidate = (root / normalized).resolve()
    return candidate if candidate.is_relative_to(root.resolve()) and candidate.is_file() else None


def _test_file_name(test_name: str) -> str | None:
    module = test_name.split("::", 1)[0]
    if "/" not in module and not module.endswith(".py"):
        module = module.replace(".", "/") + ".py"
    return _repo_path(module)


def _source_hunk_for_test(
    root: Path | None, test_name: str, hunks: list[EvidenceItem]
) -> tuple[EvidenceItem, str] | None:
    """Link only a direct imported function call to one changed function hunk."""
    if root is None:
        return None
    test_path = _test_file_name(test_name)
    if test_path is None:
        return None
    file = _safe_file(root, test_path)
    if file is None:
        return None
    try:
        tree = ast.parse(file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, SyntaxError):
        return None
    test_function = test_name.split("::")[-1].split("[", 1)[0]
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == test_function
    ]
    if len(functions) != 1:
        return None
    called = {
        node.func.id
        for node in ast.walk(functions[0])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    imports: dict[str, tuple[str, str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        source_path = _repo_path(node.module.replace(".", "/") + ".py")
        if source_path is None or source_path.startswith("tests/"):
            continue
        for alias in node.names:
            imports[alias.asname or alias.name] = (source_path, alias.name)

    candidates: set[tuple[str, str]] = set()
    for called_name in called & imports.keys():
        source_path, symbol = imports[called_name]
        source_file = _safe_file(root, source_path)
        if source_file is None:
            continue
        try:
            source_tree = ast.parse(source_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SyntaxError):
            continue
        definitions = [
            node
            for node in source_tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == symbol
        ]
        if len(definitions) != 1:
            continue
        definition = definitions[0]
        for hunk in hunks:
            if (
                hunk.file_path == source_path
                and hunk.line_start is not None
                and definition.lineno <= hunk.line_start <= definition.end_lineno
            ):
                candidates.add((hunk.id, symbol))
    if len(candidates) != 1:
        return None
    hunk_id, symbol = candidates.pop()
    return next(item for item in hunks if item.id == hunk_id), symbol


def build_evidence_registry(
    context: PRContext,
    static_result: StaticAnalysisResult,
    test_result: TestExecutionResult,
    static_comparison: StaticAnalysisComparison | None = None,
    test_comparison: TestExecutionComparison | None = None,
) -> EvidenceRegistry:
    """Assign stable, prompt-visible IDs from pinned deterministic inputs."""
    items = _diff_hunks(context.diff)
    hunks = list(items)
    introduced = Counter(
        item.model_dump_json()
        for item in (
            static_comparison.delta.introduced if static_comparison else []
        )
    )
    for finding in sorted(
        static_result.findings,
        key=lambda item: (
            item.file_path,
            item.line or 0,
            item.tool,
            item.rule_id,
            item.message,
        ),
    ):
        fingerprint = finding.model_dump_json()
        classification = "introduced" if introduced[fingerprint] else "existing"
        if introduced[fingerprint]:
            introduced[fingerprint] -= 1
        path = _repo_path(finding.file_path)
        if path is None:
            continue
        hunk = _matching_hunk(hunks, path, finding.line)
        items.append(
            EvidenceItem(
                id=f"E{len(items) + 1:03d}",
                type="static",
                source=finding.tool,
                classification=classification,
                rule=finding.rule_id,
                file_path=path,
                line_start=finding.line,
                line_end=finding.line,
                related_diff_hunk=hunk.id if hunk else None,
                severity=finding.severity,
                message=finding.message,
            )
        )

    regressions = set(
        test_comparison.introduced_test_regressions if test_comparison else []
    )
    head_failures = set(test_result.failed_tests) | set(test_result.error_tests)
    base_failures = (
        test_comparison.base.testcase_failures() if test_comparison else set()
    )
    for test_name in sorted(head_failures):
        is_regression = test_name in regressions
        association = (
            _source_hunk_for_test(context.repo_path, test_name, hunks)
            if is_regression
            else None
        )
        hunk, symbol = association if association else (None, None)
        items.append(
            EvidenceItem(
                id=f"E{len(items) + 1:03d}",
                type="test_regression" if is_regression else "test_observation",
                source="pytest",
                classification=(
                    "introduced_regression"
                    if is_regression
                    else ("existing_failure" if test_name in base_failures else "unverified")
                ),
                file_path=hunk.file_path if hunk else None,
                line_start=hunk.line_start if hunk else None,
                line_end=hunk.line_end if hunk else None,
                test_name=test_name,
                test_file_path=_test_file_name(test_name),
                base_result=(
                    "passed"
                    if is_regression
                    else ("failed" if test_name in base_failures else "not_confirmed")
                ),
                head_result="failed" if test_name in test_result.failed_tests else "error",
                related_diff_hunk=hunk.id if hunk else None,
                related_symbol=symbol,
                message=(
                    f"{test_name}: BASE PASS → HEAD FAIL."
                    if is_regression
                    else f"{test_name}: HEAD failure; BASE pass not confirmed."
                ),
            )
        )
    return EvidenceRegistry(items=items)
