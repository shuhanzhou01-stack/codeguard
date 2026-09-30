from __future__ import annotations

from llm.models import ReviewFinding, ReviewReport
from services import (
    claim_analysis_run,
    get_review_report,
    persist_review_report,
    request_pull_request_analysis,
    save_pull_request,
)

PR_DATA = {
    "number": 2,
    "title": "Example",
    "user": {"login": "dev"},
    "state": "open",
}


def test_database_service_persists_report_and_deduplicates_findings(db):
    save_pull_request(db, "acme/widget", PR_DATA)
    run = request_pull_request_analysis(
        db, "acme/widget", 2, base_sha="base", head_sha="head"
    )
    assert run is not None
    item = ReviewFinding(
        category="bug",
        severity="medium",
        title="Off by one",
        file_path="app.py",
        line_start=8,
        line_end=8,
        description="Loop includes the sentinel.",
        evidence="Changed loop uses <=.",
        evidence_ids=["E001"],
        resolved_evidence=[
            {"id": "E001", "type": "diff_hunk", "file_path": "app.py"}
        ],
        suggestion="Use <.",
        confidence=0.8,
    )
    report = ReviewReport(
        summary="One finding",
        risk_level="medium",
        findings=[item, item.model_copy()],
        test_summary={"status": "passed"},
        static_summary={"finding_count": 0},
        evidence_registry=[
            {"id": "E001", "type": "diff_hunk", "file_path": "app.py"}
        ],
        model="fake",
        prompt_version="v1",
    )
    persist_review_report(db, run.id, report)
    stored = get_review_report(db, run.id)
    assert stored is not None
    assert len(stored.findings) == 1
    assert len(stored.findings[0].fingerprint) == 64
    assert stored.evidence_registry[0]["id"] == "E001"
    assert stored.findings[0].evidence_ids == ["E001"]
    assert stored.findings[0].resolved_evidence[0]["file_path"] == "app.py"


def test_analysis_run_atomic_claim_succeeds_exactly_once(session_factory):
    setup = session_factory()
    save_pull_request(setup, "acme/widget", PR_DATA)
    run = request_pull_request_analysis(
        setup, "acme/widget", 2, base_sha="base", head_sha="head"
    )
    assert run is not None
    run_id = run.id
    setup.close()

    first = session_factory()
    second = session_factory()
    try:
        claims = [
            claim_analysis_run(first, run_id),
            claim_analysis_run(second, run_id),
        ]
        assert claims.count(True) == 1
        assert claims.count(False) == 1
        second.expire_all()
        assert second.get(type(run), run_id).status == "running"
    finally:
        first.close()
        second.close()
