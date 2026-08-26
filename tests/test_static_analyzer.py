from __future__ import annotations

import json

import pytest

from analysis.static_analyzer import StaticAnalyzer


def test_static_tools_unavailable_do_not_fail_analysis(tmp_path):
    def missing(*args, **kwargs):
        raise FileNotFoundError

    result = StaticAnalyzer(command_runner=missing).analyze(tmp_path)
    assert result.findings == []
    assert {tool.status for tool in result.tools} == {"unavailable"}
    assert {tool.tool for tool in result.tools} == {"ruff", "bandit"}


@pytest.mark.parametrize(
    "filename",
    ["tests/test_demo.py", "test_demo.py", "demo_app/calculator_test.py"],
)
def test_bandit_b101_is_ignored_in_test_files(tmp_path, filename):
    output = json.dumps(
        {
            "results": [
                {
                    "filename": filename,
                    "line_number": 1,
                    "test_id": "B101",
                    "issue_severity": "LOW",
                    "issue_text": "Use of assert detected.",
                }
            ]
        }
    )

    findings = StaticAnalyzer()._parse_bandit(output, tmp_path)

    assert findings == []


def test_bandit_keeps_other_rules_in_tests_and_b101_in_application_code(tmp_path):
    output = json.dumps(
        {
            "results": [
                {
                    "filename": "tests/test_demo.py",
                    "line_number": 2,
                    "test_id": "B105",
                    "issue_severity": "LOW",
                    "issue_text": "Possible hardcoded password.",
                },
                {
                    "filename": "demo_app/example.py",
                    "line_number": 1,
                    "test_id": "B101",
                    "issue_severity": "LOW",
                    "issue_text": "Use of assert detected.",
                },
            ]
        }
    )

    findings = StaticAnalyzer()._parse_bandit(output, tmp_path)

    assert [(finding.file_path, finding.rule_id) for finding in findings] == [
        ("tests/test_demo.py", "B105"),
        ("demo_app/example.py", "B101"),
    ]
