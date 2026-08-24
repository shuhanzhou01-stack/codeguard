from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Self


@dataclass
class RepositoryWorkspace:
    temp_root: Path
    root_path: Path
    repository: str
    head_sha: str
    base_sha: str | None = None
    _cleaned: bool = False

    def cleanup(self) -> None:
        if not self._cleaned:
            shutil.rmtree(self.temp_root, ignore_errors=True)
            self._cleaned = True

    def find_file(self, relative_path: str) -> Path | None:
        candidate = (self.root_path / relative_path).resolve()
        root = self.root_path.resolve()
        if not candidate.is_relative_to(root):
            raise ValueError(f"Unsafe repository path: {relative_path}")
        return candidate if candidate.is_file() else None

    def list_python_files(self) -> list[Path]:
        return sorted(
            path
            for path in self.root_path.rglob("*.py")
            if path.is_file() and not path.is_symlink()
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.cleanup()
