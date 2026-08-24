from __future__ import annotations

from pathlib import Path

from context.compression import compress_diff
from context.models import ChangedFileContext, PRContext
from context.repo_instructions import read_repo_instructions
from workspace import RepositoryWorkspace


def build_pr_context(
    repository: str,
    pr_number: int,
    pr_data: dict,
    changed_files: list[dict],
    diff: str,
    workspace: RepositoryWorkspace | None = None,
    repo_path: str | Path | None = None,
    instruction_repo_path: str | Path | None = None,
    context_budget: int = 120_000,
) -> PRContext:
    structured_files = [
        ChangedFileContext(
            filename=item["filename"],
            status=item.get("status", "modified"),
            additions=item.get("additions", 0),
            deletions=item.get("deletions", 0),
            patch=item.get("patch"),
        )
        for item in changed_files
    ]
    compressed_diff, compression = compress_diff(
        diff,
        [item.filename for item in structured_files],
        context_budget,
    )
    resolved_repo_path = workspace.root_path if workspace else (
        Path(repo_path) if repo_path else None
    )
    resolved_instruction_path = (
        Path(instruction_repo_path)
        if instruction_repo_path is not None
        else resolved_repo_path
    )
    instructions = (
        read_repo_instructions(resolved_instruction_path)
        if resolved_instruction_path is not None
        else None
    )

    return PRContext(
        repository=repository,
        pr_number=pr_number,
        title=pr_data.get("title") or "",
        body=pr_data.get("body") or "",
        author=(pr_data.get("user") or {}).get("login", "unknown"),
        base_sha=(pr_data.get("base") or {}).get("sha", ""),
        head_sha=(pr_data.get("head") or {}).get("sha", ""),
        changed_files=structured_files,
        diff=compressed_diff,
        repo_path=resolved_repo_path,
        repo_instructions=instructions,
        compression=compression,
    )
