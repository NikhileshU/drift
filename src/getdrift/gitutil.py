"""Thin wrappers over the plain `git` CLI.

Drift deliberately shells out to `git` rather than depending on a git library:
the tool only ever needs two facts (where the repo root is, and what HEAD is).
"""

import subprocess
from pathlib import Path
from typing import Optional


class GitError(RuntimeError):
    """Raised when git is unavailable or the cwd is not inside a git repo."""


def _git(*args: str, cwd: Optional[Path] = None) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:  # pragma: no cover - environment dependent
        raise GitError("`git` was not found on PATH. Drift requires git.") from exc
    if proc.returncode != 0:
        raise GitError(
            "git " + " ".join(args) + " failed: " + (proc.stderr.strip() or "unknown error")
        )
    return proc.stdout.strip()


def repo_root(cwd: Optional[Path] = None) -> Path:
    """Absolute path of the enclosing git repository's top level."""
    try:
        return Path(_git("rev-parse", "--show-toplevel", cwd=cwd)).resolve()
    except GitError as exc:
        raise GitError(
            "Not inside a git repository. Drift keys every snapshot to a commit "
            "hash, so it must be run from within a git repo."
        ) from exc


def head_hash(cwd: Optional[Path] = None) -> str:
    """Full 40-character commit hash of HEAD."""
    try:
        return _git("rev-parse", "HEAD", cwd=cwd)
    except GitError as exc:
        raise GitError(
            "Could not read the current commit (`git rev-parse HEAD`). "
            "Are you inside a git repository with at least one commit?"
        ) from exc
