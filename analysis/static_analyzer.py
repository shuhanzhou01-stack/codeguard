from __future__ import annotations

import json

# Subprocesses use fixed argv lists, shell=False, and bounded timeouts.
import subprocess  # nosec B404
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from analysis.models import StaticAnalysisResult, StaticFinding, ToolExecution
from security.redaction import sanitize_exception, sanitize_text

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class StaticAnalyzer:
    def __init__(
        self,
        timeout_seconds: int = 120,
        command_runner: CommandRunner = subprocess.run,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.command_runner = command_runner

    def analyze(self, repo_path: Path) -> StaticAnalysisResult:
        started = time.perf_counter()
        findings: list[StaticFinding] = []
        tools: list[ToolExecution] = []
        versions: dict[str, str] = {}

        for tool_name, command, parser in (
            (
                "ruff",
                ["ruff", "check", "--output-format=json", "."],
                self._parse_ruff,
            ),
            (
                "bandit",
                ["bandit", "-r", ".", "-f", "json", "-q"],
                self._parse_bandit,
            ),
        ):
            tool_started = time.perf_counter()
            try:
                version = self._version(tool_name, repo_path)
                completed = self.command_runner(
                    command,
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout_seconds,
                    check=False,
                    shell=False,
                )
                parsed = parser(completed.stdout, repo_path)
                findings.extend(parsed)
                status = "completed" if completed.returncode in {0, 1} else "failed"
                error = (
                    sanitize_text(completed.stderr.strip(), max_length=2_000)
                    if status == "failed"
                    else None
                )
                tools.append(
                    ToolExecution(
                        tool=tool_name,
                        status=status,
                        version=version,
                        duration_ms=int((time.perf_counter() - tool_started) * 1000),
                        error=error,
                    )
                )
                if version:
                    versions[tool_name] = version
            except FileNotFoundError:
                tools.append(
                    ToolExecution(
                        tool=tool_name,
                        status="unavailable",
                        duration_ms=int((time.perf_counter() - tool_started) * 1000),
                        error=f"{tool_name} is not installed",
                    )
                )
            # A third-party tool/parser failure is non-critical evidence.
            except Exception as error:  # noqa: BLE001
                tools.append(
                    ToolExecution(
                        tool=tool_name,
                        status="failed",
                        duration_ms=int((time.perf_counter() - tool_started) * 1000),
                        error=sanitize_exception(error),
                    )
                )

        return StaticAnalysisResult(
            findings=findings,
            duration_ms=int((time.perf_counter() - started) * 1000),
            tool_versions=versions,
            tools=tools,
        )

    def _version(self, tool_name: str, repo_path: Path) -> str | None:
        result = self.command_runner(
            [tool_name, "--version"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
            shell=False,
        )
        output = (result.stdout or result.stderr).strip()
        return output.splitlines()[0][:200] if output else None

    @staticmethod
    def _relative(path_value: str, repo_path: Path) -> str:
        path = Path(path_value)
        if not path.is_absolute():
            return path.as_posix().removeprefix("./")
        try:
            return path.resolve().relative_to(repo_path.resolve()).as_posix()
        except ValueError:
            return path.name

    def _parse_ruff(self, output: str, repo_path: Path) -> list[StaticFinding]:
        payload = json.loads(output or "[]")
        return [
            StaticFinding(
                tool="ruff",
                file_path=self._relative(item.get("filename", "unknown"), repo_path),
                line=(item.get("location") or {}).get("row"),
                rule_id=item.get("code") or "unknown",
                severity="medium",
                message=item.get("message") or "Ruff finding",
            )
            for item in payload
        ]

    def _parse_bandit(self, output: str, repo_path: Path) -> list[StaticFinding]:
        payload = json.loads(output or "{}")
        findings: list[StaticFinding] = []
        for item in payload.get("results", []):
            file_path = self._relative(item.get("filename", "unknown"), repo_path)
            rule_id = item.get("test_id") or "unknown"
            if rule_id == "B101" and self._is_test_file(file_path):
                continue
            findings.append(
                StaticFinding(
                    tool="bandit",
                    file_path=file_path,
                    line=item.get("line_number"),
                    rule_id=rule_id,
                    severity=(item.get("issue_severity") or "unknown").lower(),
                    message=item.get("issue_text") or "Bandit finding",
                )
            )
        return findings

    @staticmethod
    def _is_test_file(file_path: str) -> bool:
        path = PurePosixPath(file_path.replace("\\", "/"))
        return "tests" in path.parts or (
            path.suffix == ".py"
            and (path.name.startswith("test_") or path.name.endswith("_test.py"))
        )
