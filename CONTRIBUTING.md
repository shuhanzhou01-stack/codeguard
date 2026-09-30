# Contributing to CodeGuard

Thanks for helping improve CodeGuard. Please keep changes focused and explain how you verified them.

## Local setup

Use Python 3.13 and a virtual environment. Clone the repository, install `requirements-dev.txt`, and copy `.env.example` to a local `.env` only when you need the service stack. The offline test suite does not need a real GitHub token or LLM key. For the API, worker, PostgreSQL, Redis, and Ollama setup, follow the [Quick start](README.md#quick-start).

On Windows PowerShell:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe scripts/run_bandit.py
.\.venv\Scripts\python.exe scripts/check_repo_hygiene.py
```

## Integration tests

PostgreSQL tests require a running test database and `CODEGUARD_TEST_DATABASE_URL`. Docker tests require a reachable Docker engine, the test-runner image, and `CODEGUARD_RUN_DOCKER_TESTS=1`:

```powershell
docker build -f Dockerfile.test-runner -t codeguard-test-runner:latest .
$env:CODEGUARD_RUN_DOCKER_TESTS = "1"
.\.venv\Scripts\python.exe -m pytest -m docker -q
```

Run the full suite with both integration settings when your change affects execution, database persistence, or the review pipeline. CI runs the offline quality suite; its Docker job is available through manual workflow dispatch.

## Issues and pull requests

Before opening an issue, check for an existing report. Include a minimal reproduction, expected and actual behavior, relevant version/OS/provider details, and sanitized logs. For a pull request, describe the behavior changed, the tests run, and any effect on BASE/HEAD comparison or evidence grounding. Prefer small, reviewable changes and add meaningful tests for behavior changes.

Treat changes to repository ingestion, Docker isolation, secret handling, prompt construction, and evidence grounding as security-sensitive. Do not weaken a validation rule just to make a fixture pass. Never commit credentials or put real secrets in test fixtures, screenshots, logs, or issue reports. If a report would expose an exploitable vulnerability or private data, do not post those details in a public issue; contact the maintainer privately through an available GitHub channel.
