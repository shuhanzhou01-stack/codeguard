from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ChangedFileContext(BaseModel):
    filename: str
    status: str
    additions: int = 0
    deletions: int = 0
    patch: str | None = None

    def to_prompt_metadata(self) -> dict[str, str | int]:
        """Return bounded file metadata without duplicating the raw patch."""
        return {
            "filename": self.filename,
            "status": self.status,
            "additions": self.additions,
            "deletions": self.deletions,
        }


class CompressionMetadata(BaseModel):
    was_compressed: bool = False
    budget_chars: int
    original_chars: int
    compressed_chars: int
    included_files: list[str] = Field(default_factory=list)
    omitted_files: list[str] = Field(default_factory=list)
    included_hunks: int = 0
    omitted_hunks: int = 0


class PRContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    repository: str
    pr_number: int
    title: str
    body: str = ""
    author: str
    base_sha: str
    head_sha: str
    changed_files: list[ChangedFileContext]
    diff: str
    repo_path: Path | None = None
    repo_instructions: str | None = None
    test_evidence: dict | None = None
    static_analysis_evidence: dict | None = None
    compression: CompressionMetadata
