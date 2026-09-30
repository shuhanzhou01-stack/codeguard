from __future__ import annotations

import subprocess  # nosec B404
import sys
from pathlib import Path, PurePosixPath

FORBIDDEN_PARTS = {
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".codeguard_tmp",
    "htmlcov",
    ".vscode",
    ".fleet",
}
FORBIDDEN_NAMES = {
    ".env",
    ".coverage",
    "coverage.xml",
    "id_rsa",
    "id_ed25519",
    ".npmrc",
    ".pypirc",
}
FORBIDDEN_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3"}
MAX_FILE_BYTES = 10 * 1024 * 1024


def candidate_files(repo_root: Path) -> list[str]:
    result = subprocess.run(  # nosec B603, B607
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        shell=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Repository hygiene check requires an initialized Git repository")
    return [line for line in result.stdout.splitlines() if line]


def check_repo(repo_root: Path) -> list[str]:
    violations: list[str] = []
    for raw_path in candidate_files(repo_root):
        path = PurePosixPath(raw_path.replace("\\", "/"))
        if path.as_posix() == ".codeguard_tmp/.gitkeep":
            continue
        name = path.name.lower()
        if (
            name in FORBIDDEN_NAMES
            or (name.startswith(".env.") and name != ".env.example")
            or path.suffix.lower() in FORBIDDEN_SUFFIXES
            or name.endswith((".sqlite-wal", ".sqlite-shm", ".db-journal"))
            or FORBIDDEN_PARTS.intersection(path.parts)
        ):
            violations.append(f"forbidden generated/private path: {path.as_posix()}")
            continue
        absolute = repo_root.joinpath(*path.parts)
        if absolute.is_file() and absolute.stat().st_size > MAX_FILE_BYTES:
            violations.append(f"file exceeds 10 MiB: {path.as_posix()}")
    return violations


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    violations = check_repo(repo_root)
    if violations:
        print("Repository hygiene check failed:")
        for violation in violations:
            print(f"- {violation}")
        return 1
    print("Repository hygiene check passed (paths and file sizes only).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
