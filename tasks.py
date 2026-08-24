from __future__ import annotations

from datetime import datetime, timezone

from analysis.pipeline import run_analysis_pipeline
from celery_app import celery_app
from database import SessionLocal
from db_models import TestExecutionDB
from security.redaction import sanitize_exception, sanitize_text
from test_runner import run_pytest


@celery_app.task(name="codeguard.run_analysis")
def run_analysis(analysis_run_id: int) -> None:
    run_analysis_pipeline(analysis_run_id)


@celery_app.task(name="codeguard.run_test_execution")
def run_test_execution(analysis_run_id: int, files: dict[str, str]) -> int:
    """Preserved compatibility task for direct file-based pytest execution."""
    db = SessionLocal()
    test_execution = TestExecutionDB(
        analysis_run_id=analysis_run_id,
        status="running",
        started_at=datetime.now(timezone.utc),
        backend="local_docker",
    )
    try:
        db.add(test_execution)
        db.commit()
        db.refresh(test_execution)
        result = run_pytest(files)
        test_execution.exit_code = result.exit_code
        test_execution.stdout = sanitize_text(result.stdout)
        test_execution.stderr = sanitize_text(result.stderr)
        test_execution.timed_out = result.timed_out
        test_execution.finished_at = datetime.now(timezone.utc)
        test_execution.status = (
            "timed_out" if result.timed_out else (
                "passed" if result.exit_code == 0 else "failed"
            )
        )
        db.commit()
        return test_execution.id
    except Exception as error:  # noqa: BLE001 - Celery task boundary
        db.rollback()
        if test_execution.id is not None:
            persisted = db.get(TestExecutionDB, test_execution.id)
            if persisted is not None:
                persisted.status = "failed"
                persisted.finished_at = datetime.now(timezone.utc)
                db.commit()
        raise RuntimeError(sanitize_exception(error)) from None
    finally:
        db.close()
