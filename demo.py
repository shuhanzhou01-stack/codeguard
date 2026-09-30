from __future__ import annotations

import io
import json
import os
import tarfile
from dataclasses import replace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from analysis.evidence_registry import build_evidence_registry
from analysis.models import StaticAnalysisResult, StaticFinding, ToolExecution
from analysis.pipeline import AnalysisPipeline
from analysis.review_engine import ReviewEngine
from config import Settings
from context.builder import build_pr_context
from database import Base
from execution.base import ExecutionBackend, TestExecutionResult
from llm.client import FakeLLMProvider
from services import get_review_report, request_pull_request_analysis, save_pull_request

PR_DATA = {
    "number": 7,
    "title": "Harden command execution",
    "body": "Offline CodeGuard V1 demo",
    "user": {"login": "demo-user"},
    "state": "open",
    "base": {"sha": "base-demo"},
    "head": {"sha": "head-demo"},
}
DIFF = """diff --git a/runner.py b/runner.py
index 1111111..2222222 100644
--- a/runner.py
+++ b/runner.py
@@ -1,2 +1,4 @@
+import subprocess
+
+def run(value):
+    return subprocess.run(value, shell=True)
"""


def _archive() -> bytes:
    stream = io.BytesIO()
    files = {
        "fixture-repo/runner.py": "import subprocess\n\ndef run(value):\n    return subprocess.run(value, shell=True)\n",
        "fixture-repo/AGENTS.md": "Do not invoke a shell with user-controlled input.\n",
    }
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return stream.getvalue()


class FakeGitHubClient:
    def get_compare(
        self, owner: str, repo: str, base_sha: str, head_sha: str
    ) -> dict:
        return {
            "files": [
                {
                    "filename": "runner.py",
                    "status": "modified",
                    "additions": 4,
                    "deletions": 0,
                    "patch": DIFF,
                }
            ]
        }

    def get_compare_diff(
        self, owner: str, repo: str, base_sha: str, head_sha: str
    ) -> str:
        return DIFF

    def get_repository_tarball(self, owner: str, repo: str, ref: str) -> bytes:
        if ref not in {PR_DATA["base"]["sha"], PR_DATA["head"]["sha"]}:
            raise ValueError("Pipeline did not use pinned commit SHAs")
        return _archive()


class FakeStaticAnalyzer:
    def analyze(self, repo_path: object) -> StaticAnalysisResult:
        return StaticAnalysisResult(
            findings=[
                StaticFinding(
                    tool="bandit",
                    file_path="runner.py",
                    line=4,
                    rule_id="B602",
                    severity="high",
                    message="subprocess call with shell=True",
                )
            ],
            tools=[ToolExecution(tool="bandit", status="completed", version="fixture")],
        )


class FakeExecutionBackend(ExecutionBackend):
    name = "fake_sandbox"

    def run_tests(self, repo_path: object, timeout_seconds: int) -> TestExecutionResult:
        return TestExecutionResult(
            status="tests_passed",
            exit_code=0,
            stdout="1 passed",
            duration_ms=5,
            backend=self.name,
        )


def run_demo() -> dict:
    os.environ.setdefault(
        "CODEGUARD_TEMP_DIR", os.path.join(os.getcwd(), ".codeguard_tmp")
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)  # Demo-only ephemeral schema; production uses Alembic.
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    db = sessions()
    try:
        save_pull_request(db, "codeguard/demo", PR_DATA)
        analysis_run = request_pull_request_analysis(
            db,
            "codeguard/demo",
            7,
            base_sha=PR_DATA["base"]["sha"],
            head_sha=PR_DATA["head"]["sha"],
        )
        if analysis_run is None:
            raise RuntimeError("Demo could not create an AnalysisRun")
        run_id = analysis_run.id
    finally:
        db.close()

    fixture_context = build_pr_context(
        repository="codeguard/demo",
        pr_number=7,
        pr_data=PR_DATA,
        changed_files=[{"filename": "runner.py", "status": "modified"}],
        diff=DIFF,
    )
    fixture_registry = build_evidence_registry(
        fixture_context,
        FakeStaticAnalyzer().analyze(None),
        FakeExecutionBackend().run_tests(None, 1),
    )
    fixture_evidence_ids = [
        item.id
        for item in fixture_registry.items
        if item.file_path == "runner.py" and item.type in {"diff_hunk", "static"}
    ]
    response = {
        "summary": "Shell execution is directly reachable from the changed function.",
        "risk_level": "high",
        "findings": [
            {
                "category": "security",
                "severity": "high",
                "title": "Shell command injection",
                "description": "The changed function passes its argument to a shell.",
                "suggestion": "Use a fixed argv list and shell=False.",
                "confidence": 0.99,
                "evidence_ids": fixture_evidence_ids,
            }
        ],
    }
    settings = replace(
        Settings.from_env(),
        publish_comments=False,
        enable_static_analysis=True,
        enable_tests=True,
        llm_provider="fake",
    )
    AnalysisPipeline(
        session_factory=sessions,
        settings=settings,
        github_client=FakeGitHubClient(),
        static_analyzer=FakeStaticAnalyzer(),
        execution_backend=FakeExecutionBackend(),
        review_engine=ReviewEngine(FakeLLMProvider(response)),
    ).run(run_id)

    db = sessions()
    try:
        report = get_review_report(db, run_id)
        if report is None:
            raise RuntimeError("Demo pipeline did not persist a report")
        result_payload = {
            "analysis_run_id": run_id,
            "status": report.analysis_run.status,
            "summary": report.summary,
            "risk_level": report.risk_level,
            "test_summary": report.test_summary,
            "static_summary": report.static_summary,
            "findings": [
                {
                    "title": finding.title,
                    "file_path": finding.file_path,
                    "line_start": finding.line_start,
                    "fingerprint": finding.fingerprint,
                }
                for finding in report.findings
            ],
        }
    finally:
        db.close()
        engine.dispose()
    return result_payload


if __name__ == "__main__":
    print(json.dumps(run_demo(), indent=2))
