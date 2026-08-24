import io
import shutil
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from execution.docker_backend import DEFAULT_RUNNER_IMAGE, DockerExecutionBackend

RUNNER_IMAGE = DEFAULT_RUNNER_IMAGE


@dataclass
class TestRunResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool


def _create_files_archive(
    files: dict[str, str]
) -> bytes:
    archive_stream = io.BytesIO()

    with tarfile.open(
        fileobj=archive_stream,
        mode="w"
    ) as archive:
        for filename, content in files.items():
            path = PurePosixPath(filename)

            if path.is_absolute() or ".." in path.parts:
                raise ValueError(
                    f"Unsafe file path: {filename}"
                )

            data = content.encode("utf-8")

            file_info = tarfile.TarInfo(
                name=str(path)
            )
            file_info.size = len(data)
            file_info.mode = 0o644

            archive.addfile(
                file_info,
                io.BytesIO(data)
            )

    return archive_stream.getvalue()


def run_pytest(
    files: dict[str, str],
    timeout_seconds: int = 60
) -> TestRunResult:
    """Compatibility wrapper around the V1 Docker execution backend."""
    temp_root = Path(tempfile.mkdtemp(prefix="codeguard_test_files_"))
    try:
        for filename, content in files.items():
            path = PurePosixPath(filename)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"Unsafe file path: {filename}")
            destination = temp_root.joinpath(*path.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
        result = DockerExecutionBackend(image=RUNNER_IMAGE).run_tests(
            temp_root, timeout_seconds
        )
        return TestRunResult(
            exit_code=result.exit_code if result.exit_code is not None else 125,
            stdout=result.stdout,
            stderr=result.stderr,
            timed_out=result.timed_out,
        )
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
