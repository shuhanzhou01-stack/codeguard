from __future__ import annotations

import io
import tarfile

import pytest

from repo_manager import safe_extract_repository
from test_runner import _create_files_archive


def _tar(name: str, content: bytes = b"x") -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        info = tarfile.TarInfo(name)
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    return stream.getvalue()


def test_safe_extract_rejects_path_traversal(tmp_path):
    with pytest.raises(ValueError, match="Unsafe archive member"):
        safe_extract_repository(_tar("../../outside.txt"), tmp_path / "destination")
    assert not (tmp_path / "outside.txt").exists()


def test_safe_extract_discovers_tarball_top_directory(tmp_path):
    root = safe_extract_repository(_tar("owner-repo-sha/app.py"), tmp_path / "repo")
    assert root.name == "owner-repo-sha"
    assert (root / "app.py").read_text() == "x"


def test_file_archive_rejects_unsafe_input_path():
    with pytest.raises(ValueError, match="Unsafe file path"):
        _create_files_archive({"../escape.py": "pass"})
