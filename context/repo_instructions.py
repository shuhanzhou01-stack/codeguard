from __future__ import annotations

from pathlib import Path

INSTRUCTION_FILES = ("AGENTS.md", "CODEGUARD.md")
MAX_INSTRUCTION_CHARS = 50_000


def read_repo_instructions(repo_root: Path) -> str | None:
    sections: list[str] = []
    remaining = MAX_INSTRUCTION_CHARS
    for filename in INSTRUCTION_FILES:
        path = repo_root / filename
        if not path.is_file() or path.is_symlink():
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        content = content[:remaining]
        sections.append(f"## {filename}\n\n{content}")
        remaining -= len(content)
        if remaining <= 0:
            break
    return "\n\n".join(sections) or None
