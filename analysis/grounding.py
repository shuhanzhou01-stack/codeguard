from __future__ import annotations

import re
from pathlib import PurePosixPath

from analysis.models import (
    StaticAnalysisComparison,
    StaticAnalysisResult,
    StaticFinding,
)
from context.models import PRContext
from execution.base import TestExecutionComparison, TestExecutionResult
from llm.models import EvidenceReference, ReviewFinding

DIFF_FILE_RE = re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE)
HUNK_HEADER_RE = re.compile(
    r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,(?P<count>\d+))? @@",
    re.MULTILINE,
)
RULE_ID_RE = re.compile(r"\b[A-Z][A-Z0-9_-]*\d{2,5}\b", re.IGNORECASE)
ATTRIBUTION_RE = re.compile(
    r"\b(introduced|new(?:ly)? issue|regression|caused by (?:this|the) (?:pr|pull request))\b",
    re.IGNORECASE,
)
WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:/")


def _normalize_repo_path(value: str) -> str | None:
    normalized = value.strip().replace("\\", "/")
    if not normalized or normalized.startswith("/") or WINDOWS_ABSOLUTE_RE.match(normalized):
        return None
    path = PurePosixPath(normalized)
    if path.is_absolute() or ".." in path.parts:
        return None
    cleaned = path.as_posix().removeprefix("./")
    return cleaned if cleaned not in {"", "."} else None


def _diff_line_ranges(diff: str) -> dict[str, list[tuple[int, int]]]:
    file_matches = list(DIFF_FILE_RE.finditer(diff))
    ranges: dict[str, list[tuple[int, int]]] = {}
    for index, match in enumerate(file_matches):
        end = file_matches[index + 1].start() if index + 1 < len(file_matches) else len(diff)
        path = _normalize_repo_path(match.group(2))
        if path is None:
            continue
        ranges.setdefault(path, [])
        for hunk in HUNK_HEADER_RE.finditer(diff, match.end(), end):
            start = int(hunk.group("start"))
            count = int(hunk.group("count") or "1")
            if count > 0:
                ranges[path].append((start, start + count - 1))
    return ranges


def _range_overlaps(
    line_start: int, line_end: int, ranges: list[tuple[int, int]]
) -> bool:
    return any(line_start <= end and start <= line_end for start, end in ranges)


def _static_reference_matches(
    reference: EvidenceReference,
    file_path: str,
    findings: list[StaticFinding],
    *,
    allow_tool_only: bool = True,
) -> bool:
    identifier = (reference.identifier or "").strip().lower()
    if not identifier:
        return False
    for item in findings:
        item_path = _normalize_repo_path(item.file_path)
        if item_path != file_path:
            continue
        candidates = {
            item.rule_id.lower(),
            f"{item_path}:{item.rule_id.lower()}",
            f"{item.tool.lower()}:{item.rule_id.lower()}",
        }
        if allow_tool_only:
            candidates.add(item.tool.lower())
        if identifier in candidates:
            return True
    return False


def _test_identifier(value: str | None) -> str:
    return (value or "").strip().replace("\\", "/").lower()


class FindingGroundingValidator:
    """Deterministically validate LLM locations and evidence references."""

    def validate_findings(
        self,
        findings: list[ReviewFinding],
        context: PRContext,
        static_result: StaticAnalysisResult,
        test_result: TestExecutionResult,
        static_comparison: StaticAnalysisComparison | None = None,
        test_comparison: TestExecutionComparison | None = None,
    ) -> list[ReviewFinding]:
        grounded: list[ReviewFinding] = []
        for finding in findings:
            try:
                grounded.append(
                    self.validate_finding(
                        finding,
                        context,
                        static_result,
                        test_result,
                        static_comparison,
                        test_comparison,
                    )
                )
            except Exception as error:  # noqa: BLE001 - one finding is non-critical
                grounded.append(
                    finding.model_copy(
                        update={
                            "file_path": None,
                            "line_start": None,
                            "line_end": None,
                            "grounding_status": "ungrounded",
                            "grounding_notes": (
                                "Grounding validator rejected the finding: "
                                f"{type(error).__name__}."
                            )[:1_000],
                        }
                    )
                )
        return grounded

    def validate_finding(
        self,
        finding: ReviewFinding,
        context: PRContext,
        static_result: StaticAnalysisResult,
        test_result: TestExecutionResult,
        static_comparison: StaticAnalysisComparison | None = None,
        test_comparison: TestExecutionComparison | None = None,
    ) -> ReviewFinding:
        notes: list[str] = []
        penalties = 0
        normalized_path = (
            _normalize_repo_path(finding.file_path) if finding.file_path else None
        )
        changed_paths = {
            path
            for item in context.changed_files
            if (path := _normalize_repo_path(item.filename)) is not None
        }
        repo_file_exists = False
        if normalized_path and context.repo_path is not None:
            try:
                candidate = (context.repo_path / normalized_path).resolve()
                root = context.repo_path.resolve()
                repo_file_exists = candidate.is_relative_to(root) and candidate.is_file()
            except (OSError, ValueError):
                repo_file_exists = False

        if normalized_path is None or (
            normalized_path not in changed_paths and not repo_file_exists
        ):
            return finding.model_copy(
                update={
                    "file_path": None,
                    "line_start": None,
                    "line_end": None,
                    "grounding_status": "ungrounded",
                    "grounding_notes": (
                        "File path is unsafe or absent from changed/repository files."
                    ),
                }
            )

        diff_ranges = _diff_line_ranges(context.diff)
        line_start = finding.line_start
        line_end = finding.line_end
        if line_start is not None:
            candidate_end = line_end or line_start
            if not _range_overlaps(
                line_start, candidate_end, diff_ranges.get(normalized_path, [])
            ):
                notes.append("Line range is outside available diff hunks and was cleared.")
                line_start = None
                line_end = None
                penalties += 1
        elif normalized_path not in changed_paths:
            notes.append("Repository file is valid but is outside the changed-file diff.")
            penalties += 1

        valid_count, valid_sources, invalid_references = self._validate_references(
            finding.evidence_sources,
            normalized_path,
            diff_ranges,
            static_result,
            test_result,
            static_comparison,
            test_comparison,
        )
        if not finding.evidence_sources:
            notes.append(
                "No structured evidence source was supplied; full grounding is unavailable."
            )
            penalties += 1
        elif valid_count == 0:
            return finding.model_copy(
                update={
                    "file_path": normalized_path,
                    "line_start": line_start,
                    "line_end": line_end,
                    "grounding_status": "ungrounded",
                    "grounding_notes": " ".join(invalid_references)[:1_000],
                }
            )
        if invalid_references:
            notes.extend(invalid_references)
            penalties += 1

        claim_text = f"{finding.title} {finding.description} {finding.evidence}"
        evidence_lower = finding.evidence.lower()
        mentioned_rules = {item.lower() for item in RULE_ID_RE.findall(finding.evidence)}
        static_ids = {
            item.rule_id.lower()
            for item in static_result.findings
            if _normalize_repo_path(item.file_path) == normalized_path
        }
        mentions_static = any(
            token in evidence_lower for token in ("bandit", "ruff", "static analysis")
        ) or bool({"static", "static_delta"} & valid_sources)
        mentions_test = any(
            token in evidence_lower
            for token in ("pytest", "test passed", "tests passed", "test failed", "tests failed")
        ) or bool({"test", "test_delta"} & valid_sources)

        if mentions_static and mentioned_rules and not mentioned_rules.issubset(static_ids):
            notes.append("Evidence cites a static rule that was not recorded on HEAD.")
            penalties += 1
        if ATTRIBUTION_RE.search(claim_text):
            if mentions_static and "static_delta" not in valid_sources:
                notes.append(
                    "Introduced static attribution lacks matching static_delta evidence."
                )
                penalties += 1
            if mentions_test and "test_delta" not in valid_sources:
                notes.append(
                    "Introduced test attribution lacks matching test_delta evidence."
                )
                penalties += 1

        if not notes:
            notes.append("File, line range, and structured evidence are grounded.")
        return finding.model_copy(
            update={
                "file_path": normalized_path,
                "line_start": line_start,
                "line_end": line_end,
                "grounding_status": (
                    "grounded" if penalties == 0 else "partially_grounded"
                ),
                "grounding_notes": " ".join(notes)[:1_000],
            }
        )

    @staticmethod
    def _validate_references(
        references: list[EvidenceReference],
        file_path: str,
        diff_ranges: dict[str, list[tuple[int, int]]],
        static_result: StaticAnalysisResult,
        test_result: TestExecutionResult,
        static_comparison: StaticAnalysisComparison | None,
        test_comparison: TestExecutionComparison | None,
    ) -> tuple[int, set[str], list[str]]:
        valid_count = 0
        valid_sources: set[str] = set()
        invalid: list[str] = []
        head_tests = {
            _test_identifier(item)
            for item in (
                test_result.passed_tests
                + test_result.failed_tests
                + test_result.error_tests
                + test_result.skipped_tests
            )
        }
        introduced_tests = {
            _test_identifier(item)
            for item in (
                test_comparison.introduced_test_regressions
                if test_comparison is not None
                else []
            )
        }

        for reference in references:
            is_valid = False
            identifier = (reference.identifier or "").strip()
            if reference.source == "diff":
                reference_path = identifier
                reference_line: int | None = None
                if ":" in identifier:
                    candidate_path, candidate_line = identifier.rsplit(":", 1)
                    if candidate_line.isdigit():
                        reference_path = candidate_path
                        reference_line = int(candidate_line)
                normalized_reference = _normalize_repo_path(reference_path)
                is_valid = normalized_reference == file_path and file_path in diff_ranges
                if is_valid and reference_line is not None:
                    is_valid = _range_overlaps(
                        reference_line,
                        reference_line,
                        diff_ranges.get(file_path, []),
                    )
            elif reference.source == "static":
                is_valid = _static_reference_matches(
                    reference, file_path, static_result.findings
                )
            elif reference.source == "static_delta":
                is_valid = bool(
                    static_comparison
                    and _static_reference_matches(
                        reference,
                        file_path,
                        static_comparison.delta.introduced,
                        allow_tool_only=False,
                    )
                )
            elif reference.source == "test":
                is_valid = bool(identifier) and _test_identifier(identifier) in head_tests
            elif reference.source == "test_delta":
                is_valid = (
                    bool(identifier)
                    and _test_identifier(identifier) in introduced_tests
                )

            if is_valid:
                valid_count += 1
                valid_sources.add(reference.source)
            else:
                invalid.append(
                    f"{reference.source} evidence reference "
                    f"{reference.identifier or '(unspecified)'} does not match recorded evidence."
                )
        return valid_count, valid_sources, invalid
