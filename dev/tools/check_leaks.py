"""Fail when a file in this repository names something that belongs to the private side.

The forbidden terms are the data source, the acquisition provider, storage and account identifiers.
They are listed as SHA-256 digests of the lower-cased term, so the list can be public without
spelling them out. This is a guard against accidents, not a secret: a short word's digest is easy to
reverse by guessing.

A term matches as a whole token, either a word (split on anything that is not a letter or digit) or
a dotted/hyphenated identifier, so both a domain's first label and a bucket name are caught.

Usage: ``python dev/tools/check_leaks.py`` from the repository root; exit 1 on any leak.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DIGESTS_FILE = Path(__file__).with_name("forbidden_digests.txt")

_WORD = re.compile(r"[a-z0-9]+")
_IDENTIFIER = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)+")
_DIGEST_LINE = re.compile(r"[0-9a-f]{64}")


def load_digests(path: Path) -> set[str]:
    """Read one hex SHA-256 per line; ``#`` comments and blank lines are ignored."""
    digests: set[str] = set()
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if not _DIGEST_LINE.fullmatch(line):
            raise ValueError(f"{path}:{number}: not a lower-case hex SHA-256 digest")
        digests.add(line)
    return digests


def _tokens(line: str) -> Iterable[str]:
    lowered = line.lower()
    yield from _WORD.findall(lowered)
    yield from _IDENTIFIER.findall(lowered)


def find_leaks(text: str, digests: set[str]) -> list[int]:
    """Return the 1-based numbers of the lines that contain a forbidden token."""
    return [
        number
        for number, line in enumerate(text.splitlines(), start=1)
        if any(hashlib.sha256(token.encode()).hexdigest() in digests for token in _tokens(line))
    ]


def _repository_files(repo: Path) -> list[str]:
    """Tracked files plus untracked ones not ignored, so a leak is caught before its first commit."""
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return [name for name in listing.stdout.decode().split("\0") if name]


def main(
    repo: Path = REPO_ROOT,
    digests_file: Path = DIGESTS_FILE,
    *,
    files: list[str] | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """Report every leaking line as ``path:line``, never the term itself; return the exit status."""
    digests = load_digests(digests_file)
    leaks = []
    for name in files if files is not None else _repository_files(repo):
        path = repo / name
        if not path.is_file():
            continue
        # latin-1 decodes any byte sequence, so a binary file is scanned rather than skipped.
        text = path.read_bytes().decode("latin-1")
        leaks.extend(f"{name}:{number}" for number in find_leaks(text, digests))
    for leak in leaks:
        out(f"forbidden term at {leak}")
    if leaks:
        out(f"{len(leaks)} line(s) name something that belongs to the private side.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
