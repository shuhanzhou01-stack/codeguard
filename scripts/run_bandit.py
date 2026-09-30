"""Run Bandit on publishable Python files, excluding ignored local environments."""

from __future__ import annotations

import subprocess  # nosec B404
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(  # nosec B603, B607
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
        shell=False,
    )
    paths = sorted(
        path.decode("utf-8")
        for path in result.stdout.split(b"\x00")
        if path
        and path.endswith(b".py")
        and not path.replace(b"\\", b"/").startswith(b"tests/")
        and (root / path.decode("utf-8")).is_file()
    )
    return subprocess.run(  # nosec B603
        [sys.executable, "-m", "bandit", "-q", *paths],
        cwd=root,
        check=False,
        shell=False,
    ).returncode


if __name__ == "__main__":
    sys.exit(main())
