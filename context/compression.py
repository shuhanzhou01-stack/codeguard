from __future__ import annotations

import re
from dataclasses import dataclass, field

from context.models import CompressionMetadata

DIFF_FILE_RE = re.compile(r"(?m)^diff --git a/(.+?) b/(.+?)$")
HUNK_RE = re.compile(r"(?m)^@@ .+? @@.*$")


@dataclass
class DiffSection:
    path: str
    header: str
    hunks: list[str] = field(default_factory=list)


def _split_diff(diff: str) -> list[DiffSection]:
    matches = list(DIFF_FILE_RE.finditer(diff))
    if not matches:
        return [DiffSection(path="unknown", header="", hunks=[diff])] if diff else []

    sections: list[DiffSection] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(diff)
        block = diff[match.start():end].rstrip()
        hunk_matches = list(HUNK_RE.finditer(block))
        if not hunk_matches:
            sections.append(DiffSection(path=match.group(2), header=block))
            continue
        header = block[:hunk_matches[0].start()].rstrip()
        hunks = []
        for hunk_index, hunk_match in enumerate(hunk_matches):
            hunk_end = (
                hunk_matches[hunk_index + 1].start()
                if hunk_index + 1 < len(hunk_matches)
                else len(block)
            )
            hunks.append(block[hunk_match.start():hunk_end].rstrip())
        sections.append(DiffSection(path=match.group(2), header=header, hunks=hunks))
    return sections


def compress_diff(
    diff: str,
    changed_file_paths: list[str],
    budget_chars: int,
) -> tuple[str, CompressionMetadata]:
    budget_chars = max(1, budget_chars)
    sections = _split_diff(diff)
    priority = {path: index for index, path in enumerate(changed_file_paths)}
    sections.sort(key=lambda item: priority.get(item.path, len(priority)))

    if len(diff) <= budget_chars:
        metadata = CompressionMetadata(
            budget_chars=budget_chars,
            original_chars=len(diff),
            compressed_chars=len(diff),
            included_files=[section.path for section in sections],
            included_hunks=sum(len(section.hunks) for section in sections),
        )
        return diff, metadata

    selected: list[str] = []
    included_files: list[str] = []
    omitted_files: list[str] = []
    included_hunks = 0
    omitted_hunks = 0
    used = 0

    for section in sections:
        units = [section.header] if section.header else []
        units.extend(section.hunks)
        included_for_file: list[str] = []
        for unit in units:
            addition = unit + "\n"
            if unit and used + len(addition) <= budget_chars:
                included_for_file.append(unit)
                used += len(addition)
                if unit in section.hunks:
                    included_hunks += 1
            elif unit in section.hunks:
                omitted_hunks += 1

        if included_for_file:
            selected.append("\n".join(included_for_file))
            included_files.append(section.path)
        else:
            omitted_files.append(section.path)

    marker = (
        "\n# CodeGuard compression: omitted complete hunks/files that exceeded "
        f"the {budget_chars}-character context budget.\n"
    )
    compressed = "\n".join(selected)
    if len(compressed) + len(marker) <= budget_chars:
        compressed += marker
    compressed = compressed.rstrip()

    metadata = CompressionMetadata(
        was_compressed=True,
        budget_chars=budget_chars,
        original_chars=len(diff),
        compressed_chars=len(compressed),
        included_files=included_files,
        omitted_files=omitted_files,
        included_hunks=included_hunks,
        omitted_hunks=omitted_hunks,
    )
    return compressed, metadata
