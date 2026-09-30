from __future__ import annotations

import io
import tarfile

import docker
import pytest

from execution.docker_backend import (
    DockerExecutionBackend,
    create_repository_archive,
    parse_pytest_junit_xml,
)
from execution.environment import (
    dependency_cache_key,
    detect_dependency_manifest,
)


def test_requirements_manifest_fingerprint_and_cache_key_are_stable(tmp_path):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("requests==2.34.2\n", encoding="utf-8")
    first = detect_dependency_manifest(tmp_path)
    second = detect_dependency_manifest(tmp_path)
    assert first is not None and second is not None
    assert first.type == "requirements"
    assert first.fingerprint == second.fingerprint
    assert dependency_cache_key(first) == dependency_cache_key(second)

    requirements.write_text("requests==2.34.1\n", encoding="utf-8")
    changed = detect_dependency_manifest(tmp_path)
    assert changed is not None
    assert changed.fingerprint != first.fingerprint
    assert dependency_cache_key(changed) != dependency_cache_key(first)


def test_standard_pyproject_dependencies_are_detected(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='sample'\nversion='1.0.0'\n"
        "dependencies=['requests==2.34.2']\n",
        encoding="utf-8",
    )
    manifest = detect_dependency_manifest(tmp_path)
    assert manifest is not None
    assert manifest.type == "pyproject"
    assert manifest.path == "pyproject.toml"


def test_missing_dependency_manifest_is_explicit(tmp_path):
    assert detect_dependency_manifest(tmp_path) is None


@pytest.mark.parametrize("secret_name", [".env", ".env.production", "id_rsa", "private.pem"])
def test_repository_archive_rejects_secret_files(tmp_path, secret_name):
    (tmp_path / "test_ok.py").write_text("def test_ok(): assert True\n")
    (tmp_path / secret_name).write_text("sensitive value\n")
    with pytest.raises(ValueError, match="sensitive file"):
        create_repository_archive(tmp_path)


def test_repository_archive_keeps_safe_example(tmp_path):
    (tmp_path / ".env.example").write_text("GITHUB_TOKEN=\n")
    with tarfile.open(fileobj=io.BytesIO(create_repository_archive(tmp_path))) as archive:
        assert ".env.example" in archive.getnames()


def test_pytest_junit_xml_records_testcase_level_outcomes():
    parsed = parse_pytest_junit_xml(
        b"""<?xml version='1.0' encoding='utf-8'?>
        <testsuites><testsuite>
          <testcase classname="tests.test_auth" name="test_pass" />
          <testcase classname="tests.test_auth" name="test_fail"><failure /></testcase>
          <testcase classname="tests.test_auth" name="test_error"><error /></testcase>
          <testcase classname="tests.test_auth" name="test_skip"><skipped /></testcase>
        </testsuite></testsuites>"""
    )
    assert parsed == {
        "passed_tests": ["tests.test_auth::test_pass"],
        "failed_tests": ["tests.test_auth::test_fail"],
        "error_tests": ["tests.test_auth::test_error"],
        "skipped_tests": ["tests.test_auth::test_skip"],
    }


def test_dependency_build_uses_generated_spec_not_repository_dockerfile(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests==2.34.2\n")
    (tmp_path / "Dockerfile").write_text("RUN REPOSITORY_DOCKERFILE_SENTINEL\n")
    manifest = detect_dependency_manifest(tmp_path)
    assert manifest is not None
    context = DockerExecutionBackend._dependency_build_context(manifest)
    with tarfile.open(fileobj=io.BytesIO(context), mode="r") as archive:
        names = archive.getnames()
        dockerfile = archive.extractfile("Dockerfile")
        assert dockerfile is not None
        generated = dockerfile.read().decode()
    assert names == ["Dockerfile", "codeguard-requirements.txt"]
    assert "REPOSITORY_DOCKERFILE_SENTINEL" not in generated
    assert "pip install" in generated


class FailingImages:
    def get(self, tag):
        raise docker.errors.ImageNotFound("missing")

    def build(self, **kwargs):
        raise docker.errors.BuildError("dependency build failed", [])


class FailingClient:
    images = FailingImages()


def test_dependency_install_failure_has_explicit_preparation_status(tmp_path):
    (tmp_path / "requirements.txt").write_text("missing-package==1.0\n")
    _, status, manifest, cache_key, error = DockerExecutionBackend()._prepare_image(
        FailingClient(), tmp_path
    )
    assert status == "dependency_install_failed"
    assert manifest is not None
    assert cache_key is not None
    assert "BuildError" in error
