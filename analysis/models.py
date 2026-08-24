from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, Field


class StaticFinding(BaseModel):
    tool: str
    file_path: str
    line: int | None = None
    rule_id: str
    severity: str
    message: str

    def fingerprint(self) -> str:
        canonical = {
            "tool": self.tool.strip().lower(),
            "file_path": self.file_path.replace("\\", "/").removeprefix("./").lower(),
            "rule_id": self.rule_id.strip().lower(),
            "message": re.sub(r"\s+", " ", self.message.strip().lower()),
        }
        encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


class ToolExecution(BaseModel):
    tool: str
    status: Literal["completed", "unavailable", "failed"]
    version: str | None = None
    duration_ms: int = 0
    error: str | None = None


class StaticAnalysisResult(BaseModel):
    findings: list[StaticFinding] = Field(default_factory=list)
    duration_ms: int = 0
    tool_versions: dict[str, str] = Field(default_factory=dict)
    tools: list[ToolExecution] = Field(default_factory=list)

    def summary(self) -> dict:
        by_severity: dict[str, int] = {}
        for finding in self.findings:
            key = finding.severity.lower()
            by_severity[key] = by_severity.get(key, 0) + 1
        return {
            "finding_count": len(self.findings),
            "by_severity": by_severity,
            "duration_ms": self.duration_ms,
            "tools": [tool.model_dump() for tool in self.tools],
        }


class StaticFindingDelta(BaseModel):
    existing: list[StaticFinding] = Field(default_factory=list)
    resolved: list[StaticFinding] = Field(default_factory=list)
    introduced: list[StaticFinding] = Field(default_factory=list)

    def summary(self) -> dict[str, int]:
        return {
            "existing": len(self.existing),
            "resolved": len(self.resolved),
            "introduced": len(self.introduced),
        }


class StaticAnalysisComparison(BaseModel):
    base: StaticAnalysisResult
    head: StaticAnalysisResult
    delta: StaticFindingDelta

    def summary(self) -> dict:
        return {
            "base": self.base.summary(),
            "head": self.head.summary(),
            "delta": self.delta.summary(),
        }


def compare_static_results(
    base: StaticAnalysisResult, head: StaticAnalysisResult
) -> StaticAnalysisComparison:
    base_groups: dict[str, list[StaticFinding]] = defaultdict(list)
    head_groups: dict[str, list[StaticFinding]] = defaultdict(list)
    for item in base.findings:
        base_groups[item.fingerprint()].append(item)
    for item in head.findings:
        head_groups[item.fingerprint()].append(item)

    def ordered(items: list[StaticFinding]) -> list[StaticFinding]:
        return sorted(
            items,
            key=lambda item: (
                item.line is None,
                item.line or 0,
                item.model_dump_json(),
            ),
        )

    existing: list[StaticFinding] = []
    resolved: list[StaticFinding] = []
    introduced: list[StaticFinding] = []
    for fingerprint in sorted(base_groups.keys() | head_groups.keys()):
        base_items = ordered(base_groups[fingerprint])
        head_items = ordered(head_groups[fingerprint])
        matched_count = min(len(base_items), len(head_items))
        existing.extend(head_items[:matched_count])
        resolved.extend(base_items[matched_count:])
        introduced.extend(head_items[matched_count:])
    return StaticAnalysisComparison(
        base=base,
        head=head,
        delta=StaticFindingDelta(
            existing=existing,
            resolved=resolved,
            introduced=introduced,
        ),
    )
