from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text

import tasks
from demo import run_demo
from execution.docker_backend import DockerExecutionBackend


@pytest.mark.integration
def test_offline_end_to_end_demo():
    result = run_demo()
    assert result["status"] == "completed"
    assert result["findings"][0]["file_path"] == "runner.py"


@pytest.mark.integration
def test_celery_eager_task(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(tasks, "run_analysis_pipeline", lambda run_id: calls.append(run_id))
    result = tasks.run_analysis.apply(args=[42], throw=True)
    assert result.successful()
    assert calls == [42]


@pytest.mark.postgres
def test_optional_postgres_connection():
    url = os.getenv("CODEGUARD_TEST_DATABASE_URL")
    if not url:
        pytest.skip("CODEGUARD_TEST_DATABASE_URL is not configured")
    engine = create_engine(url)
    with engine.connect() as connection:
        assert connection.scalar(text("select 1")) == 1
    engine.dispose()


@pytest.mark.docker
def test_optional_docker_runner(tmp_path):
    if os.getenv("CODEGUARD_RUN_DOCKER_TESTS") != "1":
        pytest.skip("Set CODEGUARD_RUN_DOCKER_TESTS=1 to run Docker integration")
    (tmp_path / "test_ok.py").write_text(
        "import socket\n"
        "from pathlib import Path\n"
        "import pytest\n\n"
        "def test_network_is_disabled():\n"
        "    with pytest.raises(OSError):\n"
        "        socket.create_connection(('1.1.1.1', 53), timeout=0.2)\n\n"
        "def test_repository_mount_is_read_only():\n"
        "    with pytest.raises(OSError):\n"
        "        Path('write-probe.txt').write_text('no')\n"
    )
    result = DockerExecutionBackend().run_tests(tmp_path, timeout_seconds=30)
    assert result.status == "tests_passed", result.stderr
    assert result.preparation_status == "dependency_manifest_missing"
    assert len(result.passed_tests) == 2


@pytest.mark.docker
def test_optional_docker_runner_installs_detected_dependencies(tmp_path):
    if os.getenv("CODEGUARD_RUN_DOCKER_TESTS") != "1":
        pytest.skip("Set CODEGUARD_RUN_DOCKER_TESTS=1 to run Docker integration")
    (tmp_path / "requirements.txt").write_text(
        "requests==2.34.2\n", encoding="utf-8"
    )
    (tmp_path / "test_dependency.py").write_text(
        "import requests\n\ndef test_dependency(): assert requests.__version__ == '2.34.2'\n",
        encoding="utf-8",
    )
    result = DockerExecutionBackend().run_tests(tmp_path, timeout_seconds=30)
    assert result.status == "tests_passed", result.stderr
    assert result.preparation_status == "prepared"
    assert result.dependency_manifest["type"] == "requirements"
    assert result.cache_key is not None
