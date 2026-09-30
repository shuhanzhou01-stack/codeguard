from __future__ import annotations

from collections import defaultdict

from analysis.evidence_registry import EvidenceItem, EvidenceRegistry
from analysis.grounding import ATTRIBUTION_RE, FindingGroundingValidator
from analysis.models import StaticAnalysisComparison, StaticAnalysisResult
from context.models import PRContext
from execution.base import TestExecutionComparison, TestExecutionResult
from llm.models import EvidenceReference, LLMFindingProposal, ReviewFinding
from security.redaction import sanitize_text

SEVERITY_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _references(items: list[EvidenceItem]) -> list[EvidenceReference]:
    references: list[EvidenceReference] = []
    for item in items:
        if item.type == "diff_hunk" and item.file_path:
            identifier = item.file_path
            if item.line_start is not None:
                identifier += f":{item.line_start}"
            references.append(EvidenceReference(source="diff", identifier=identifier))
        elif item.type == "static" and item.rule:
            references.append(
                EvidenceReference(
                    source=(
                        "static_delta"
                        if item.classification == "introduced"
                        else "static"
                    ),
                    identifier=item.rule,
                )
            )
        elif item.type == "test_regression" and item.test_name:
            references.append(
                EvidenceReference(source="test_delta", identifier=item.test_name)
            )
        elif item.type == "test_observation" and item.test_name:
            references.append(
                EvidenceReference(source="test", identifier=item.test_name)
            )
        if item.related_diff_hunk and item.file_path and item.line_start is not None:
            references.append(
                EvidenceReference(
                    source="diff", identifier=f"{item.file_path}:{item.line_start}"
                )
            )
    return [
        EvidenceReference(source=source, identifier=identifier)
        for source, identifier in dict.fromkeys(
            (ref.source, ref.identifier) for ref in references
        )
    ]


def _location(items: list[EvidenceItem]) -> tuple[str | None, int | None, int | None]:
    located = [item for item in items if item.file_path]
    paths = {item.file_path for item in located}
    if len(paths) != 1:
        return None, None, None
    path = next(iter(paths))
    exact_static = {
        (item.line_start, item.line_end)
        for item in located
        if item.type == "static" and item.line_start is not None
    }
    if len(exact_static) == 1:
        start, end = next(iter(exact_static))
        return path, start, end
    hunks = {
        item.related_diff_hunk or item.id
        for item in located
        if item.type in {"diff_hunk", "test_regression"}
    }
    if len(hunks) == 1:
        ranged = [item for item in located if item.line_start is not None]
        if ranged:
            return path, ranged[0].line_start, ranged[0].line_end
    return path, None, None


def _deterministic_evidence(items: list[EvidenceItem]) -> str:
    parts = []
    for item in items:
        location = item.file_path or item.test_file_path or "test-level"
        if item.line_start is not None and item.file_path:
            location += f":{item.line_start}"
        rule = f" {item.rule}" if item.rule else ""
        test = f" {item.test_name}" if item.test_name else ""
        outcome = (
            f" BASE={item.base_result} HEAD={item.head_result}"
            if item.head_result
            else ""
        )
        parts.append(
            f"{item.id} [{item.classification} {item.source}{rule}] "
            f"{location}{test}{outcome}: {item.message}"
        )
    return sanitize_text("; ".join(parts), max_length=8_000)


def resolve_finding(
    proposal: LLMFindingProposal,
    registry: EvidenceRegistry,
    context: PRContext,
    static_result: StaticAnalysisResult,
    test_result: TestExecutionResult,
    static_comparison: StaticAnalysisComparison | None,
    test_comparison: TestExecutionComparison | None,
    validator: FindingGroundingValidator,
) -> ReviewFinding:
    ids = list(dict.fromkeys(proposal.evidence_ids))
    items = [item for evidence_id in ids if (item := registry.get(evidence_id))]
    invalid = [evidence_id for evidence_id in ids if registry.get(evidence_id) is None]
    path, start, end = _location(items) if not invalid else (None, None, None)
    static_severities = [
        item.severity.lower()
        for item in items
        if item.type == "static"
        and item.severity
        and item.severity.lower() in SEVERITY_RANK
    ]
    severity = (
        max(static_severities, key=SEVERITY_RANK.get)
        if static_severities
        else proposal.severity
    )
    finding = ReviewFinding(
        category=proposal.category,
        severity=severity,
        title=proposal.title,
        file_path=path,
        line_start=start,
        line_end=end,
        description=proposal.description,
        evidence=_deterministic_evidence(items),
        suggestion=proposal.suggestion,
        confidence=proposal.confidence,
        evidence_sources=_references(items),
        evidence_ids=ids,
        resolved_evidence=[
            item.model_copy(update={"message": sanitize_text(item.message)}).model_dump(
                exclude_none=True
            )
            for item in items
        ],
    )
    if invalid or not items:
        reason = (
            "Unknown evidence IDs: " + ", ".join(invalid)
            if invalid
            else "No evidence IDs were supplied."
        )
        return finding.model_copy(
            update={
                "file_path": None,
                "line_start": None,
                "line_end": None,
                "grounding_status": "ungrounded",
                "grounding_notes": reason,
            }
        )

    if path is None:
        if all(item.type in {"test_regression", "test_observation"} for item in items):
            claims_introduction = bool(
                ATTRIBUTION_RE.search(
                    f"{proposal.title} {proposal.description}"
                )
            )
            proven = all(item.type == "test_regression" for item in items)
            status = "grounded" if proven or not claims_introduction else "ungrounded"
            return finding.model_copy(
                update={
                    "grounding_status": status,
                    "grounding_notes": (
                        "Exact testcase evidence IDs verified; source location "
                        "is unavailable and was not inferred."
                        if status == "grounded"
                        else "Introduced regression was claimed without BASE-pass evidence."
                    ),
                }
            )
        return finding.model_copy(
            update={
                "grounding_status": "partially_grounded",
                "grounding_notes": (
                    "Evidence IDs exist, but their source locations conflict "
                    "or cannot be resolved to one file."
                ),
            }
        )
    return validator.validate_finding(
        finding,
        context,
        static_result,
        test_result,
        static_comparison,
        test_comparison,
    )


def _regression_key(
    finding: ReviewFinding, registry: EvidenceRegistry
) -> tuple[str, str, str] | None:
    items = [registry.get(evidence_id) for evidence_id in finding.evidence_ids]
    if any(item is None for item in items):
        return None
    regressions = [item for item in items if item and item.type == "test_regression"]
    if not regressions or any(
        item and item.type not in {"test_regression", "diff_hunk"} for item in items
    ):
        return None
    associations = {
        (item.related_diff_hunk, item.related_symbol)
        for item in regressions
    }
    if len(associations) != 1:
        return None
    hunk_id, symbol = next(iter(associations))
    if not hunk_id or not symbol:
        return None
    return finding.category.lower(), hunk_id, symbol


def resolve_findings(
    proposals: list[LLMFindingProposal],
    registry: EvidenceRegistry,
    context: PRContext,
    static_result: StaticAnalysisResult,
    test_result: TestExecutionResult,
    static_comparison: StaticAnalysisComparison | None,
    test_comparison: TestExecutionComparison | None,
    validator: FindingGroundingValidator,
) -> list[ReviewFinding]:
    grouped: dict[tuple[str, str, str], list[LLMFindingProposal]] = defaultdict(list)
    ordered: list[LLMFindingProposal | tuple[str, str, str]] = []
    for proposal in proposals:
        preliminary = resolve_finding(
            proposal, registry, context, static_result, test_result,
            static_comparison, test_comparison, validator,
        )
        key = _regression_key(preliminary, registry)
        if key is None:
            ordered.append(proposal)
        else:
            if key not in grouped:
                ordered.append(key)
            grouped[key].append(proposal)

    resolved: list[ReviewFinding] = []
    exact_seen: set[tuple[str, tuple[str, ...]]] = set()
    for entry in ordered:
        if isinstance(entry, tuple):
            group = grouped[entry]
            ids = list(dict.fromkeys(
                evidence_id
                for proposal in group
                for evidence_id in proposal.evidence_ids
            ))
            proposal = group[0].model_copy(update={"evidence_ids": ids})
        else:
            proposal = entry
        signature = (proposal.category.lower(), tuple(sorted(set(proposal.evidence_ids))))
        if signature in exact_seen:
            continue
        exact_seen.add(signature)
        resolved.append(
            resolve_finding(
                proposal, registry, context, static_result, test_result,
                static_comparison, test_comparison, validator,
            )
        )
    return resolved
