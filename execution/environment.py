from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

import tomllib
from pydantic import BaseModel, Field


class DependencyManifest(BaseModel):
    type: Literal["requirements", "pyproject"]
    path: str
    fingerprint: str
    dependencies: list[str] = Field(default_factory=list, exclude=True)

    def prompt_metadata(self) -> dict[str, str]:
        return {
            "type": self.type,
            "path": self.path,
            "fingerprint": self.fingerprint,
        }


def _fingerprint(manifest_type: str, content: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(manifest_type.encode())
    digest.update(b"\0")
    digest.update(content)
    return digest.hexdigest()


def detect_dependency_manifest(repo_path: Path) -> DependencyManifest | None:
    requirements = repo_path / "requirements.txt"
    if requirements.is_file() and not requirements.is_symlink():
        content = requirements.read_bytes()
        return DependencyManifest(
            type="requirements",
            path="requirements.txt",
            fingerprint=_fingerprint("requirements", content),
            dependencies=requirements.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines(),
        )

    pyproject = repo_path / "pyproject.toml"
    if pyproject.is_file() and not pyproject.is_symlink():
        content = pyproject.read_bytes()
        payload = tomllib.loads(content.decode("utf-8", errors="strict"))
        dependencies = (payload.get("project") or {}).get("dependencies")
        if isinstance(dependencies, list) and all(
            isinstance(item, str) for item in dependencies
        ):
            return DependencyManifest(
                type="pyproject",
                path="pyproject.toml",
                fingerprint=_fingerprint("pyproject", content),
                dependencies=dependencies,
            )
    return None


def dependency_cache_key(
    manifest: DependencyManifest, python_version: str = "3.13"
) -> str:
    digest = hashlib.sha256(
        f"python={python_version}\nmanifest={manifest.fingerprint}".encode()
    ).hexdigest()
    return f"py{python_version.replace('.', '')}-{digest[:24]}"


def render_install_requirements(manifest: DependencyManifest) -> str:
    return "\n".join(manifest.dependencies).strip() + "\n"
