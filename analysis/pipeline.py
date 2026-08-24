from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session, sessionmaker

from analysis.models import StaticAnalysisResult, compare_static_results
from analysis.report_builder import build_review_comment
from analysis.review_engine import ReviewEngine
from analysis.static_analyzer import StaticAnalyzer
from config import Settings, get_settings
from context.builder import build_pr_context
from database import SessionLocal
from db_models import AnalysisRunDB, PullRequestDB, TestExecutionDB
from execution.base import (
    ExecutionBackend,
    TestExecutionResult,
    compare_test_results,
)
from execution.docker_backend import DockerExecutionBackend
from github_client import GitHubClient
from llm.client import create_llm_provider
from repo_manager import create_repository_workspace
from security.redaction import sanitize_exception, sanitize_text
from services import claim_analysis_run, persist_review_report
from workspace import RepositoryWorkspace

logger = logging.getLogger("codeguard.pipeline")
DIFF_PATH_RE = re.compile(r"(?m)^diff --git a/(.+?) b/(.+?)$")


def _complete_changed_files(changed_files: list[dict], diff: str) -> list[dict]:
    completed = list(changed_files)
    known = {item.get("filename") for item in completed}
    for match in DIFF_PATH_RE.finditer(diff):
        path = match.group(2)
        if path not in known:
            completed.append(
                {
                    "filename": path,
                    "status": "modified",
                    "additions": 0,
                    "deletions": 0,
                }
            )
            known.add(path)
    return completed


class AnalysisPipeline:
    def __init__(
        self,
        session_factory: sessionmaker[Session] = SessionLocal,
        settings: Settings | None = None,
        github_client: GitHubClient | None = None,
        static_analyzer: StaticAnalyzer | None = None,
        execution_backend: ExecutionBackend | None = None,
        review_engine: ReviewEngine | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings or get_settings()
        self.github = github_client or GitHubClient(self.settings.github_token)
        self.static_analyzer = static_analyzer or StaticAnalyzer()
        self.execution_backend = execution_backend or DockerExecutionBackend(
            memory_limit=self.settings.test_memory_limit,
            pid_limit=self.settings.test_pid_limit,
        )
        self.review_engine = review_engine or ReviewEngine(
            create_llm_provider(self.settings)
        )

    def run(self, analysis_run_id: int) -> None:
        db = self.session_factory()
        workspaces: list[RepositoryWorkspace] = []
        stage = "claim_analysis_run"
        repository = "unknown"
        pr_number: int | None = None
        base_sha: str | None = None
        head_sha: str | None = None
        started = time.perf_counter()
        try:
            if not claim_analysis_run(db, analysis_run_id):
                logger.info(
                    "analysis_claim_skipped analysis_run_id=%s", analysis_run_id
                )
                return
            db.expire_all()
            stage = "load_analysis_run"
            analysis_run = db.get(AnalysisRunDB, analysis_run_id)
            if analysis_run is None:
                logger.warning(
                    "analysis_run_not_found analysis_run_id=%s", analysis_run_id
                )
                return
            pull_request = db.get(PullRequestDB, analysis_run.pull_request_id)
            if pull_request is None:
                raise RuntimeError("Pull request record no longer exists")
            repository = pull_request.repository
            pr_number = pull_request.pr_number
            owner, repo = repository.split("/", 1)
            base_sha = analysis_run.base_sha
            head_sha = analysis_run.head_sha
            if not base_sha or not head_sha:
                raise RuntimeError("AnalysisRun does not contain pinned base/head SHAs")

            pull_request.analysis_status = "running"
            db.commit()
            self._log(
                "analysis_started",
                analysis_run_id,
                repository,
                pr_number,
                base_sha,
                head_sha,
                stage,
            )

            stage = "fetch_pinned_comparison"
            comparison_data = self.github.get_compare(
                owner, repo, base_sha, head_sha
            )
            changed_files = comparison_data.get("files") or []
            diff = self.github.get_compare_diff(owner, repo, base_sha, head_sha)
            changed_files = _complete_changed_files(changed_files, diff)

            stage = "download_base_repository"
            base_archive = self.github.get_repository_tarball(owner, repo, base_sha)
            base_workspace = create_repository_workspace(
                base_archive, repository, base_sha
            )
            workspaces.append(base_workspace)

            stage = "download_head_repository"
            head_archive = self.github.get_repository_tarball(owner, repo, head_sha)
            head_workspace = create_repository_workspace(
                head_archive, repository, head_sha, base_sha
            )
            workspaces.append(head_workspace)
            self._log(
                "repositories_downloaded",
                analysis_run_id,
                repository,
                pr_number,
                base_sha,
                head_sha,
                stage,
            )

            stage = "build_context"
            context = build_pr_context(
                repository=repository,
                pr_number=pr_number,
                pr_data={
                    "title": pull_request.title,
                    "body": "",
                    "user": {"login": pull_request.author},
                    "base": {"sha": base_sha},
                    "head": {"sha": head_sha},
                },
                changed_files=changed_files,
                diff=diff,
                workspace=head_workspace,
                instruction_repo_path=base_workspace.root_path,
                context_budget=self.settings.max_diff_size,
            )
            self._log(
                "context_built",
                analysis_run_id,
                repository,
                pr_number,
                base_sha,
                head_sha,
                stage,
            )

            stage = "static_analysis_base_head"
            if self.settings.enable_static_analysis:
                base_static = self.static_analyzer.analyze(base_workspace.root_path)
                head_static = self.static_analyzer.analyze(head_workspace.root_path)
            else:
                base_static = StaticAnalysisResult()
                head_static = StaticAnalysisResult()
            static_comparison = compare_static_results(base_static, head_static)
            context.static_analysis_evidence = static_comparison.model_dump()
            self._log(
                "static_analysis_completed",
                analysis_run_id,
                repository,
                pr_number,
                base_sha,
                head_sha,
                stage,
                introduced=len(static_comparison.delta.introduced),
                resolved=len(static_comparison.delta.resolved),
            )

            stage = "test_execution_base"
            base_test = self._execute_tests(base_workspace)
            self._persist_test_execution(
                db, analysis_run_id, base_test, "base", base_sha
            )
            stage = "test_execution_head"
            head_test = self._execute_tests(head_workspace)
            self._persist_test_execution(
                db, analysis_run_id, head_test, "head", head_sha
            )
            test_comparison = compare_test_results(base_test, head_test)
            context.test_evidence = test_comparison.model_dump()
            self._log(
                "tests_completed",
                analysis_run_id,
                repository,
                pr_number,
                base_sha,
                head_sha,
                stage,
                delta_status=test_comparison.delta_status,
            )

            stage = "llm_review"
            report = self.review_engine.review(
                context,
                head_static,
                head_test,
                static_comparison,
                test_comparison,
            )
            self._log(
                "llm_review_completed",
                analysis_run_id,
                repository,
                pr_number,
                base_sha,
                head_sha,
                stage,
            )

            stage = "persist_report"
            report_row = persist_review_report(db, analysis_run_id, report)
            self._log(
                "report_persisted",
                analysis_run_id,
                repository,
                pr_number,
                base_sha,
                head_sha,
                stage,
            )

            stage = "publish_feedback"
            if self.settings.publish_comments:
                publish_result = self.github.publish_review_comment(
                    owner, repo, pr_number, build_review_comment(report)
                )
                report_row.publish_status = publish_result.status
            else:
                report_row.publish_status = "disabled"

            analysis_run = db.get(AnalysisRunDB, analysis_run_id)
            if analysis_run is None:
                raise RuntimeError("AnalysisRun disappeared before completion")
            pull_request = db.get(PullRequestDB, analysis_run.pull_request_id)
            analysis_run.status = "completed"
            analysis_run.finished_at = datetime.now(timezone.utc)
            if pull_request is not None:
                pull_request.analysis_status = "completed"
            db.commit()
            self._log(
                "analysis_completed",
                analysis_run_id,
                repository,
                pr_number,
                base_sha,
                head_sha,
                "complete",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception as error:  # noqa: BLE001 - critical stage boundary
            db.rollback()
            safe_error = sanitize_exception(error)
            analysis_run = db.get(AnalysisRunDB, analysis_run_id)
            if analysis_run is not None:
                analysis_run.status = "failed"
                analysis_run.failed_stage = stage
                analysis_run.error_summary = safe_error
                analysis_run.finished_at = datetime.now(timezone.utc)
                pull_request = db.get(PullRequestDB, analysis_run.pull_request_id)
                if pull_request is not None:
                    pull_request.analysis_status = "failed"
                db.commit()
            logger.error(
                "analysis_failed analysis_run_id=%s repository=%s pr_number=%s "
                "base_sha=%s head_sha=%s stage=%s error=%s",
                analysis_run_id,
                repository,
                pr_number,
                base_sha,
                head_sha,
                stage,
                safe_error,
            )
            raise RuntimeError(safe_error) from None
        finally:
            for workspace in reversed(workspaces):
                workspace.cleanup()
            db.close()

    def _execute_tests(
        self, workspace: RepositoryWorkspace
    ) -> TestExecutionResult:
        if not self.settings.enable_tests:
            return TestExecutionResult(
                status="skipped",
                stderr="Test execution is disabled",
                backend="none",
            )
        if not workspace.list_python_files():
            return TestExecutionResult(
                status="skipped",
                stderr="No Python files were discovered",
                backend=self.execution_backend.name,
            )
        try:
            result = self.execution_backend.run_tests(
                workspace.root_path, self.settings.test_timeout_seconds
            )
            result.stdout = sanitize_text(result.stdout)
            result.stderr = sanitize_text(result.stderr)
            return result
        except Exception as error:  # noqa: BLE001 - isolation backend is non-critical
            return TestExecutionResult(
                status="unavailable",
                stderr=sanitize_exception(error, max_length=4_000),
                backend=self.execution_backend.name,
            )

    @staticmethod
    def _persist_test_execution(
        db: Session,
        analysis_run_id: int,
        result: TestExecutionResult,
        revision_role: str,
        commit_sha: str,
    ) -> TestExecutionDB:
        now = datetime.now(timezone.utc)
        row = TestExecutionDB(
            analysis_run_id=analysis_run_id,
            status=result.status,
            exit_code=result.exit_code,
            stdout=sanitize_text(result.stdout),
            stderr=sanitize_text(result.stderr),
            timed_out=result.timed_out,
            duration_ms=result.duration_ms,
            backend=result.backend,
            revision_role=revision_role,
            commit_sha=commit_sha,
            passed_tests=result.passed_tests,
            failed_tests=result.failed_tests,
            error_tests=result.error_tests,
            skipped_tests=result.skipped_tests,
            started_at=now,
            finished_at=now,
        )
        db.add(row)
        db.commit()
        return row

    @staticmethod
    def _log(
        event: str,
        analysis_run_id: int,
        repository: str,
        pr_number: int,
        base_sha: str,
        head_sha: str,
        stage: str,
        **extra: object,
    ) -> None:
        suffix = " ".join(
            f"{key}={sanitize_text(value, max_length=500)}"
            for key, value in sorted(extra.items())
        )
        logger.info(
            "%s analysis_run_id=%s repository=%s pr_number=%s base_sha=%s "
            "head_sha=%s stage=%s%s",
            event,
            analysis_run_id,
            repository,
            pr_number,
            base_sha,
            head_sha,
            stage,
            f" {suffix}" if suffix else "",
        )


def run_analysis_pipeline(analysis_run_id: int) -> None:
    AnalysisPipeline().run(analysis_run_id)
