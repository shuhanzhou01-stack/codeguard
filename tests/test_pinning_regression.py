from __future__ import annotations

import io
import tarfile
from dataclasses import replace

from sqlalchemy import select

from analysis.models import (
    StaticAnalysisResult,
    StaticFinding,
    compare_static_results,
)
from analysis.pipeline import AnalysisPipeline
from analysis.review_engine import ReviewEngine
from config import Settings
from db_models import AnalysisRunDB
from db_models import TestExecutionDB as ExecutionRow
from execution.base import ExecutionBackend, compare_test_results
from execution.base import TestExecutionResult as ExecutionResult
from llm.client import FakeLLMProvider
from services import request_pull_request_analysis, save_pull_request


def _archive(ref: str) -> bytes:
    stream = io.BytesIO()
    content = b"value = 1\n"
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        info = tarfile.TarInfo(f"repo-{ref}/app.py")
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    return stream.getvalue()


class PinnedGitHub:
    def __init__(self) -> None:
        self.comparisons: list[tuple[str, str]] = []
        self.tarball_refs: list[str] = []

    def get_compare(self, owner, repo, base_sha, head_sha):
        self.comparisons.append((base_sha, head_sha))
        return {
            "files": [
                {
                    "filename": "app.py",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 1,
                }
            ]
        }

    def get_compare_diff(self, owner, repo, base_sha, head_sha):
        assert (base_sha, head_sha) == self.comparisons[-1]
        return (
            "diff --git a/app.py b/app.py\n"
            "--- a/app.py\n+++ b/app.py\n"
            "@@ -1 +1 @@\n-value = 0\n+value = 1\n"
        )

    def get_repository_tarball(self, owner, repo, ref):
        self.tarball_refs.append(ref)
        return _archive(ref)


def _settings() -> Settings:
    return replace(
        Settings.from_env(),
        publish_comments=False,
        enable_static_analysis=False,
        enable_tests=False,
        llm_provider="fake",
    )


def test_worker_uses_run_pins_even_after_pr_advances(session_factory):
    db = session_factory()
    save_pull_request(
        db,
        "acme/widget",
        {
            "number": 4,
            "title": "Pinned",
            "user": {"login": "dev"},
            "state": "open",
        },
    )
    run_a = request_pull_request_analysis(
        db, "acme/widget", 4, base_sha="base-a", head_sha="head-a"
    )
    run_b = request_pull_request_analysis(
        db, "acme/widget", 4, base_sha="base-b", head_sha="head-b"
    )
    assert run_a is not None and run_b is not None
    run_a_id, run_b_id = run_a.id, run_b.id
    db.close()

    github = PinnedGitHub()
    pipeline = AnalysisPipeline(
        session_factory=session_factory,
        settings=_settings(),
        github_client=github,
        review_engine=ReviewEngine(FakeLLMProvider()),
    )
    pipeline.run(run_a_id)
    pipeline.run(run_b_id)

    assert github.comparisons == [("base-a", "head-a"), ("base-b", "head-b")]
    assert github.tarball_refs == ["base-a", "head-a", "base-b", "head-b"]
    db = session_factory()
    try:
        first = db.get(AnalysisRunDB, run_a_id)
        second = db.get(AnalysisRunDB, run_b_id)
        assert first.status == second.status == "completed"
        executions = list(
            db.scalars(
                select(ExecutionRow).order_by(
                    ExecutionRow.analysis_run_id,
                    ExecutionRow.revision_role,
                )
            )
        )
        assert {(item.revision_role, item.commit_sha) for item in executions} == {
            ("base", "base-a"),
            ("head", "head-a"),
            ("base", "base-b"),
            ("head", "head-b"),
        }
    finally:
        db.close()


class RecordingExecutionBackend(ExecutionBackend):
    name = "recording"

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def run_tests(self, repo_path, timeout_seconds):
        self.calls.append((repo_path.name, timeout_seconds))
        return ExecutionResult(
            status="tests_passed", exit_code=0, backend=self.name
        )


def test_pipeline_runs_same_backend_contract_for_base_and_head(session_factory):
    db = session_factory()
    save_pull_request(
        db,
        "acme/widget",
        {
            "number": 5,
            "title": "Compare tests",
            "user": {"login": "dev"},
            "state": "open",
        },
    )
    run = request_pull_request_analysis(
        db, "acme/widget", 5, base_sha="base-tests", head_sha="head-tests"
    )
    assert run is not None
    run_id = run.id
    db.close()
    backend = RecordingExecutionBackend()
    settings = replace(_settings(), enable_tests=True, test_timeout_seconds=77)
    AnalysisPipeline(
        session_factory=session_factory,
        settings=settings,
        github_client=PinnedGitHub(),
        execution_backend=backend,
        review_engine=ReviewEngine(FakeLLMProvider()),
    ).run(run_id)
    assert len(backend.calls) == 2
    assert [timeout for _, timeout in backend.calls] == [77, 77]


def _static(rule: str, line: int = 2) -> StaticFinding:
    return StaticFinding(
        tool="ruff",
        file_path="app.py",
        line=line,
        rule_id=rule,
        severity="medium",
        message=f"Finding {rule}",
    )


def test_static_delta_existing_resolved_and_introduced_semantics():
    base = StaticAnalysisResult(findings=[_static("F401", 1), _static("E711")])
    head = StaticAnalysisResult(findings=[_static("F401", 99), _static("S101")])
    comparison = compare_static_results(base, head)
    assert [item.rule_id for item in comparison.delta.existing] == ["F401"]
    assert [item.rule_id for item in comparison.delta.resolved] == ["E711"]
    assert [item.rule_id for item in comparison.delta.introduced] == ["S101"]


def test_static_delta_preserves_duplicate_fingerprints_as_multiset():
    base = StaticAnalysisResult(findings=[_static("F401", 1), _static("F401", 5)])
    head = StaticAnalysisResult(findings=[_static("F401", 99)])
    comparison = compare_static_results(base, head)
    assert len(comparison.delta.existing) == 1
    assert len(comparison.delta.resolved) == 1
    assert comparison.delta.introduced == []


def _test(status: str, **changes) -> ExecutionResult:
    return ExecutionResult(status=status, backend="fixture", **changes)


def test_test_delta_tracks_existing_failure_by_testcase():
    node_id = "tests/test_auth.py::test_a"
    comparison = compare_test_results(
        _test("tests_failed", failed_tests=[node_id]),
        _test("tests_failed", failed_tests=[node_id]),
    )
    assert comparison.existing_failures == [node_id]
    assert comparison.introduced_failures == []
    assert comparison.resolved_failures == []
    assert comparison.delta_status == "existing_failure"


def test_test_delta_can_resolve_and_introduce_in_same_run():
    test_a = "tests/test_auth.py::test_a"
    test_b = "tests/test_payment.py::test_b"
    comparison = compare_test_results(
        _test("tests_failed", failed_tests=[test_a], passed_tests=[test_b]),
        _test("tests_failed", failed_tests=[test_b], passed_tests=[test_a]),
    )
    assert comparison.existing_failures == []
    assert comparison.resolved_failures == [test_a]
    assert comparison.introduced_failures == [test_b]
    assert comparison.introduced_test_regressions == [test_b]
    assert comparison.delta_status == "new_failure"


def test_dependency_failure_is_not_introduced_test_regression():
    comparison = compare_test_results(
        _test("tests_passed", passed_tests=["tests/test_ok.py::test_ok"]),
        _test("dependency_install_failed"),
    )
    assert comparison.delta_status == "verification_unavailable"
    assert comparison.environment_status == "head_unavailable"
    assert comparison.introduced_test_regressions == []


def test_timeout_is_verification_unavailable_not_regression():
    comparison = compare_test_results(
        _test("tests_passed", passed_tests=["tests/test_ok.py::test_ok"]),
        _test("timed_out"),
    )
    assert comparison.delta_status == "verification_unavailable"
    assert comparison.introduced_failures == []
    assert comparison.introduced_test_regressions == []
