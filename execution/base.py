from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ExecutionStatus = Literal[
    "dependency_manifest_missing",
    "dependency_install_failed",
    "test_collection_failed",
    "tests_failed",
    "tests_passed",
    "timed_out",
    "execution_failed",
    "unavailable",
    "skipped",
    "passed",
    "failed",
]
PreparationStatus = Literal[
    "not_started",
    "dependency_manifest_missing",
    "prepared",
    "dependency_install_failed",
]
TestDeltaStatus = Literal[
    "new_failure",
    "existing_failure",
    "resolved_failure",
    "no_regression",
    "unavailable",
    "verification_unavailable",
]
EnvironmentStatus = Literal[
    "available",
    "base_unavailable",
    "head_unavailable",
    "both_unavailable",
]


class TestExecutionResult(BaseModel):
    status: ExecutionStatus
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    duration_ms: int = 0
    backend: str
    preparation_status: PreparationStatus = "not_started"
    dependency_manifest: dict | None = None
    cache_key: str | None = None
    passed_tests: list[str] = Field(default_factory=list)
    failed_tests: list[str] = Field(default_factory=list)
    error_tests: list[str] = Field(default_factory=list)
    skipped_tests: list[str] = Field(default_factory=list)

    def testcase_failures(self) -> set[str]:
        return set(self.failed_tests) | set(self.error_tests)

    def summary(self) -> dict:
        return {
            "status": self.status,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "duration_ms": self.duration_ms,
            "backend": self.backend,
            "preparation_status": self.preparation_status,
            "dependency_manifest": self.dependency_manifest,
            "cache_key": self.cache_key,
            "passed_tests": sorted(set(self.passed_tests)),
            "failed_tests": sorted(set(self.failed_tests)),
            "error_tests": sorted(set(self.error_tests)),
            "skipped_tests": sorted(set(self.skipped_tests)),
        }


class TestExecutionComparison(BaseModel):
    base: TestExecutionResult
    head: TestExecutionResult
    delta_status: TestDeltaStatus
    environment_status: EnvironmentStatus = "available"
    existing_failures: list[str] = Field(default_factory=list)
    introduced_failures: list[str] = Field(default_factory=list)
    resolved_failures: list[str] = Field(default_factory=list)
    introduced_test_regressions: list[str] = Field(default_factory=list)
    unverified_head_failures: list[str] = Field(default_factory=list)

    def summary(self) -> dict:
        return {
            "base": self.base.summary(),
            "head": self.head.summary(),
            "delta": {
                "status": self.delta_status,
                "environment_status": self.environment_status,
                "existing_failures": self.existing_failures,
                "introduced_failures": self.introduced_failures,
                "resolved_failures": self.resolved_failures,
                "introduced_test_regressions": self.introduced_test_regressions,
                "unverified_head_failures": self.unverified_head_failures,
            },
        }


TEST_RESULT_STATUSES = {"tests_passed", "tests_failed", "passed", "failed"}
ENVIRONMENT_FAILURE_STATUSES = {
    "dependency_manifest_missing",
    "dependency_install_failed",
    "test_collection_failed",
    "timed_out",
    "execution_failed",
    "unavailable",
    "skipped",
}


def compare_test_results(
    base: TestExecutionResult, head: TestExecutionResult
) -> TestExecutionComparison:
    base_available = base.status in TEST_RESULT_STATUSES
    head_available = head.status in TEST_RESULT_STATUSES
    if not base_available or not head_available:
        if not base_available and not head_available:
            environment_status: EnvironmentStatus = "both_unavailable"
        elif not base_available:
            environment_status = "base_unavailable"
        else:
            environment_status = "head_unavailable"
        return TestExecutionComparison(
            base=base,
            head=head,
            delta_status="verification_unavailable",
            environment_status=environment_status,
        )

    base_failures = base.testcase_failures()
    head_failures = head.testcase_failures()
    existing = sorted(base_failures & head_failures)
    introduced = sorted(head_failures - base_failures)
    resolved = sorted(base_failures - head_failures)

    # A set difference is useful evidence, but the stronger word "regression" is
    # reserved for a concrete node that is recorded as passing on BASE.
    regressions = sorted(set(introduced) & set(base.passed_tests))
    unverified = sorted(set(introduced) - set(regressions))

    if regressions:
        status: TestDeltaStatus = "new_failure"
    elif unverified:
        status = "verification_unavailable"
    elif existing:
        status = "existing_failure"
    elif resolved:
        status = "resolved_failure"
    else:
        status = "no_regression"
    return TestExecutionComparison(
        base=base,
        head=head,
        delta_status=status,
        existing_failures=existing,
        introduced_failures=introduced,
        resolved_failures=resolved,
        introduced_test_regressions=regressions,
        unverified_head_failures=unverified,
    )


class ExecutionBackend(ABC):
    name: str

    @abstractmethod
    def run_tests(
        self,
        repo_path: Path,
        timeout_seconds: int,
    ) -> TestExecutionResult:
        raise NotImplementedError


class GitHubActionsExecutionBackend(ExecutionBackend):
    """Reserved production-oriented backend; execution is not implemented in V1."""

    name = "github_actions"

    def run_tests(
        self,
        repo_path: Path,
        timeout_seconds: int,
    ) -> TestExecutionResult:
        return TestExecutionResult(
            status="unavailable",
            stderr="GitHubActionsExecutionBackend is a V1 interface stub",
            backend=self.name,
        )
