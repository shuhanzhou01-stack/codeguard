import io
import os
import shutil
import tarfile
import tempfile
import uuid
from pathlib import Path

from workspace import RepositoryWorkspace

MAX_ARCHIVE_MEMBERS = 50_000
MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024


def _create_temp_root() -> Path:
    configured_temp_dir = os.getenv("CODEGUARD_TEMP_DIR")
    if not configured_temp_dir:
        return Path(tempfile.mkdtemp(prefix="codeguard_repo_"))
    base = Path(configured_temp_dir).resolve()
    base.mkdir(parents=True, exist_ok=True)
    temp_root = base / f"codeguard_repo_{uuid.uuid4().hex}"
    temp_root.mkdir()
    if os.name != "nt":
        temp_root.chmod(0o700)
    return temp_root


def _safe_member_path(destination: Path, member: tarfile.TarInfo) -> Path:
    member_path = Path(member.name)
    if member_path.is_absolute() or ".." in member_path.parts:
        raise ValueError(f"Unsafe archive member: {member.name}")
    if member.issym() or member.islnk() or member.isdev():
        raise ValueError(f"Unsupported archive member: {member.name}")

    resolved = (destination / member_path).resolve()
    if not resolved.is_relative_to(destination.resolve()):
        raise ValueError(f"Archive path escapes workspace: {member.name}")
    return resolved


def _discover_repository_root(temp_root: Path) -> Path:
    entries = [entry for entry in temp_root.iterdir() if entry.name != "__MACOSX"]
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return temp_root


def safe_extract_repository(archive_bytes: bytes, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
        members = archive.getmembers()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise ValueError("Repository archive contains too many members")
        if sum(member.size for member in members if member.isfile()) > MAX_EXTRACTED_BYTES:
            raise ValueError("Repository archive exceeds the extraction size limit")
        for member in members:
            _safe_member_path(destination, member)
        archive.extractall(destination, members=members, filter="data")
    return _discover_repository_root(destination)


def create_repository_workspace(
    archive_bytes: bytes,
    repository: str,
    head_sha: str,
    base_sha: str | None = None,
) -> RepositoryWorkspace:
    temp_root = _create_temp_root()
    try:
        root_path = safe_extract_repository(archive_bytes, temp_root)
    except Exception:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise

    return RepositoryWorkspace(
        temp_root=temp_root,
        root_path=root_path,
        repository=repository,
        head_sha=head_sha,
        base_sha=base_sha,
    )


def extract_repository(
    archive_bytes: bytes
) -> str:
    """Compatibility wrapper returning the temporary extraction directory."""
    temp_root = _create_temp_root()
    try:
        safe_extract_repository(archive_bytes, temp_root)
    except Exception:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise
    return str(temp_root)
