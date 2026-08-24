from __future__ import annotations

import io
import tarfile
import time
import xml.etree.ElementTree as ET  # nosec B405
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path

import docker
import tomllib

from execution.base import ExecutionBackend, TestExecutionResult
from execution.environment import (
    DependencyManifest,
    dependency_cache_key,
    detect_dependency_manifest,
    render_install_requirements,
)
from security.redaction import sanitize_exception, sanitize_text

DEFAULT_RUNNER_IMAGE = "codeguard-test-runner:latest"
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
SKIP_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache"}
JUNIT_XML_PATH = "/tmp/codeguard-junit.xml"  # nosec B108
MAX_JUNIT_XML_BYTES = 16 * 1024 * 1024


def parse_pytest_junit_xml(xml_content: bytes) -> dict[str, list[str]]:
    """Return stable testcase identifiers from pytest's JUnit XML output."""
    if not xml_content or len(xml_content) > MAX_JUNIT_XML_BYTES:
        raise ValueError("JUnit XML is missing or exceeds the size limit")
    upper_xml = xml_content.upper()
    if any(
        marker in upper_xml
        for marker in (b"<!DOCTYPE", b"<!ENTITY", b" SYSTEM ", b" PUBLIC ")
    ):
        raise ValueError("JUnit XML contains a prohibited declaration")
    root = ET.fromstring(xml_content)  # nosec B314
    classified: dict[str, list[str]] = {
        "passed_tests": [],
        "failed_tests": [],
        "error_tests": [],
        "skipped_tests": [],
    }
    for testcase in root.iter("testcase"):
        name = (testcase.get("name") or "unknown").strip()
        scope = (
            testcase.get("file")
            or testcase.get("classname")
            or "unknown"
        ).strip().replace("\\", "/")
        identifier = f"{scope}::{name}"
        if testcase.find("failure") is not None:
            bucket = "failed_tests"
        elif testcase.find("error") is not None:
            bucket = "error_tests"
        elif testcase.find("skipped") is not None:
            bucket = "skipped_tests"
        else:
            bucket = "passed_tests"
        classified[bucket].append(identifier)
    return {key: sorted(set(values)) for key, values in classified.items()}


def create_repository_archive(repo_path: Path) -> bytes:
    stream = io.BytesIO()
    total_size = 0
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for path in sorted(repo_path.rglob("*")):
            relative = path.relative_to(repo_path)
            if any(part in SKIP_PARTS for part in relative.parts):
                continue
            if path.is_symlink() or not path.is_file():
                continue
            total_size += path.stat().st_size
            if total_size > MAX_ARCHIVE_BYTES:
                raise ValueError("Repository exceeds the test archive size limit")
            info = archive.gettarinfo(str(path), arcname=relative.as_posix())
            info.uid = 10001
            info.gid = 10001
            info.uname = "runner"
            info.gname = "runner"
            info.mode = 0o644
            with path.open("rb") as source:
                archive.addfile(info, source)
    return stream.getvalue()


class DockerExecutionBackend(ExecutionBackend):
    name = "local_docker"

    def __init__(
        self,
        image: str = DEFAULT_RUNNER_IMAGE,
        memory_limit: str = "512m",
        pid_limit: int = 128,
        network_disabled: bool = True,
    ) -> None:
        self.image = image
        self.memory_limit = memory_limit
        self.pid_limit = pid_limit
        self.network_disabled = network_disabled

    @staticmethod
    def _dependency_build_context(manifest: DependencyManifest) -> bytes:
        dockerfile = "\n".join(
            [
                f"FROM {DEFAULT_RUNNER_IMAGE}",
                "USER root",
                "COPY codeguard-requirements.txt /tmp/codeguard-requirements.txt",
                "RUN python -m pip install --no-cache-dir --disable-pip-version-check -r /tmp/codeguard-requirements.txt",
                "USER runner",
                "",
            ]
        ).encode()
        requirements = render_install_requirements(manifest).encode()
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            for name, content in (
                ("Dockerfile", dockerfile),
                ("codeguard-requirements.txt", requirements),
            ):
                info = tarfile.TarInfo(name)
                info.size = len(content)
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(content))
        return stream.getvalue()

    def _prepare_image(
        self, client: docker.DockerClient, repo_path: Path
    ) -> tuple[str, str, DependencyManifest | None, str | None, str | None]:
        try:
            manifest = detect_dependency_manifest(repo_path)
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
            return (
                self.image,
                "dependency_install_failed",
                None,
                None,
                sanitize_exception(error),
            )
        if manifest is None:
            return (
                self.image,
                "dependency_manifest_missing",
                None,
                None,
                None,
            )

        cache_key = dependency_cache_key(manifest)
        image_tag = f"codeguard-dependencies:{cache_key}"
        try:
            client.images.get(image_tag)
            return image_tag, "prepared", manifest, cache_key, None
        except docker.errors.ImageNotFound:
            pass
        try:
            client.images.build(
                fileobj=io.BytesIO(self._dependency_build_context(manifest)),
                custom_context=True,
                tag=image_tag,
                rm=True,
                forcerm=True,
                network_mode="default",
                labels={
                    "com.codeguard.purpose": "dependency-cache",
                    "com.codeguard.cache-key": cache_key,
                },
            )
            return image_tag, "prepared", manifest, cache_key, None
        except docker.errors.DockerException as error:
            return (
                self.image,
                "dependency_install_failed",
                manifest,
                cache_key,
                sanitize_exception(error),
            )

    def run_tests(
        self,
        repo_path: Path,
        timeout_seconds: int,
    ) -> TestExecutionResult:
        started = time.perf_counter()
        client = None
        container = None
        staging_container = None
        workspace_volume = None
        try:
            client = docker.from_env()
            (
                test_image,
                preparation_status,
                manifest,
                cache_key,
                preparation_error,
            ) = self._prepare_image(client, repo_path)
            manifest_metadata = manifest.prompt_metadata() if manifest else None
            if preparation_status == "dependency_install_failed":
                return TestExecutionResult(
                    status="dependency_install_failed",
                    stderr=preparation_error or "Dependency installation failed",
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    backend=self.name,
                    preparation_status="dependency_install_failed",
                    dependency_manifest=manifest_metadata,
                    cache_key=cache_key,
                )
            workspace_volume = client.volumes.create(
                labels={"com.codeguard.purpose": "test-workspace"}
            )
            staging_container = client.containers.run(
                self.image,
                command=["python", "-c", "import time; time.sleep(86400)"],
                detach=True,
                network_disabled=True,
                volumes={
                    workspace_volume.name: {"bind": "/workspace", "mode": "rw"}
                },
            )
            staging_container.put_archive(
                "/workspace", create_repository_archive(repo_path)
            )
            ownership = staging_container.exec_run(
                ["chown", "-R", "10001:10001", "/workspace"], user="root"
            )
            if ownership.exit_code != 0:
                raise RuntimeError("Unable to prepare the test workspace volume")
            staging_container.remove(force=True)
            staging_container = None

            container = client.containers.run(
                test_image,
                command=["python", "-c", "import time; time.sleep(86400)"],
                detach=True,
                network_disabled=self.network_disabled,
                mem_limit=self.memory_limit,
                pids_limit=self.pid_limit,
                read_only=True,
                volumes={
                    workspace_volume.name: {"bind": "/workspace", "mode": "ro"}
                },
                # This is a container mount point, not a host temporary file.
                tmpfs={  # nosec B108
                    "/tmp": "rw,noexec,nosuid,size=64m",
                },
                security_opt=["no-new-privileges"],
                cap_drop=["ALL"],
            )
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(
                container.exec_run,
                [
                    "python",
                    "-m",
                    "pytest",
                    "-q",
                    "-p",
                    "no:cacheprovider",
                    f"--junitxml={JUNIT_XML_PATH}",
                    "-o",
                    "junit_family=xunit2",
                ],
                workdir="/workspace",
                demux=True,
            )
            try:
                result = future.result(timeout=timeout_seconds)
                stdout_bytes, stderr_bytes = result.output
                stdout = sanitize_text(
                    stdout_bytes.decode("utf-8", errors="replace")
                    if stdout_bytes
                    else ""
                )
                stderr = sanitize_text(
                    stderr_bytes.decode("utf-8", errors="replace")
                    if stderr_bytes
                    else ""
                )
                if result.exit_code in {0, 1}:
                    xml_result = container.exec_run(["cat", JUNIT_XML_PATH])
                    if xml_result.exit_code != 0:
                        raise ValueError("pytest did not produce JUnit XML")
                    testcase_results = parse_pytest_junit_xml(xml_result.output)
                else:
                    testcase_results = {
                        "passed_tests": [],
                        "failed_tests": [],
                        "error_tests": [],
                        "skipped_tests": [],
                    }
                if result.exit_code == 0:
                    status = "tests_passed"
                elif result.exit_code == 1:
                    status = "tests_failed"
                elif result.exit_code in {2, 5}:
                    status = "test_collection_failed"
                else:
                    status = "execution_failed"
                return TestExecutionResult(
                    status=status,
                    exit_code=result.exit_code,
                    stdout=stdout,
                    stderr=stderr,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    backend=self.name,
                    preparation_status=preparation_status,
                    dependency_manifest=manifest_metadata,
                    cache_key=cache_key,
                    **testcase_results,
                )
            except TimeoutError:
                container.kill()
                return TestExecutionResult(
                    status="timed_out",
                    exit_code=124,
                    stderr="Test execution timed out",
                    timed_out=True,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    backend=self.name,
                    preparation_status=preparation_status,
                    dependency_manifest=manifest_metadata,
                    cache_key=cache_key,
                )
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
        except (docker.errors.DockerException, OSError, ValueError, ET.ParseError) as error:
            return TestExecutionResult(
                status="execution_failed",
                stderr=sanitize_exception(error, max_length=4_000),
                duration_ms=int((time.perf_counter() - started) * 1000),
                backend=self.name,
            )
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except docker.errors.DockerException:
                    pass
            if staging_container is not None:
                try:
                    staging_container.remove(force=True)
                except docker.errors.DockerException:
                    pass
            if workspace_volume is not None:
                try:
                    workspace_volume.remove(force=True)
                except docker.errors.DockerException:
                    pass
            if client is not None:
                client.close()
