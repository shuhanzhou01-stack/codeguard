from __future__ import annotations

from analysis.static_analyzer import StaticAnalyzer


def test_static_tools_unavailable_do_not_fail_analysis(tmp_path):
    def missing(*args, **kwargs):
        raise FileNotFoundError

    result = StaticAnalyzer(command_runner=missing).analyze(tmp_path)
    assert result.findings == []
    assert {tool.status for tool in result.tools} == {"unavailable"}
    assert {tool.tool for tool in result.tools} == {"ruff", "bandit"}
