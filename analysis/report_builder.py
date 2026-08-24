from __future__ import annotations

import hashlib
import json

from llm.models import ReviewFinding, ReviewReport

COMMENT_MARKER = "<!-- codeguard-review -->"
SEVERITY_RANK = {
    "info": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


def finding_fingerprint(finding: ReviewFinding) -> str:
    canonical = {
        "category": finding.category.strip().lower(),
        "file_path": (finding.file_path or "").strip().lower(),
        "line_start": finding.line_start,
        "line_end": finding.line_end,
        "title": finding.title.strip().lower(),
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def recalculate_verified_report(report: ReviewReport) -> ReviewReport:
    """Replace model-owned public risk/summary with grounded aggregation."""
    publishable = [
        finding
        for finding in report.findings
        if finding.grounding_status in {"grounded", "partially_grounded"}
    ]
    if not publishable:
        summary = "No evidence-grounded issues were identified."
        risk_level = "none"
    else:
        highest = max(
            publishable,
            key=lambda finding: SEVERITY_RANK[finding.severity],
        ).severity
        risk_level = "none" if highest == "info" else highest
        grounded = sum(item.grounding_status == "grounded" for item in publishable)
        partial = len(publishable) - grounded
        summary = (
            f"CodeGuard identified {len(publishable)} evidence-grounded issue(s): "
            f"{grounded} grounded and {partial} partially grounded."
        )
    return report.model_copy(
        update={
            "summary": summary,
            "risk_level": risk_level,
        }
    )


def build_review_comment(report: ReviewReport) -> str:
    grounding = report.grounding_summary()
    test_status = (
        (report.test_summary.get("delta") or {}).get("status")
        or report.test_summary.get("status")
        or "unknown"
    )
    static_count = (
        ((report.static_summary.get("delta") or {}).get("introduced"))
        if "delta" in report.static_summary
        else report.static_summary.get("finding_count", 0)
    )
    publishable = [
        finding
        for finding in report.findings
        if finding.grounding_status in {"grounded", "partially_grounded"}
    ]
    lines = [
        COMMENT_MARKER,
        "## CodeGuard review",
        "",
        report.summary,
        "",
        f"**Risk:** {report.risk_level}  ",
        f"**Test delta:** {test_status}  ",
        f"**Introduced/HEAD static findings:** {static_count}  ",
        (
            "**Grounding:** "
            f"{grounding['grounded']} grounded, "
            f"{grounding['partially_grounded']} partial, "
            f"{grounding['ungrounded']} ungrounded"
        ),
    ]
    if publishable:
        lines.extend(["", "### Findings", ""])
        for finding in publishable:
            location = finding.file_path or "unmapped"
            if finding.line_start:
                location += f":{finding.line_start}"
            lines.append(
                f"- **{finding.severity.upper()} — {finding.title}** "
                f"(`{location}`, {finding.grounding_status}): {finding.description}"
            )
    else:
        lines.extend(["", "No evidence-grounded findings were emitted."])
    lines.extend(["", f"_Model: {report.model}; prompt: {report.prompt_version}_"])
    return "\n".join(lines)
