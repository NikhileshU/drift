"""Thin wrappers over the plain `git` CLI.

Drift shells out to `git` rather than depending on a git library: it only ever
needs two facts — where the repo root is, and what HEAD is.
"""

import subprocess
from pathlib import Path


class GitError(RuntimeError):
    """Raised when git is unavailable or the cwd is not inside a git repo."""


def _git(*args: str) -> str:
    try:
        proc = subprocess.run(["git", *args], capture_output=True, text=True)
    except FileNotFoundError as exc:  # pragma: no cover - environment dependent
        raise GitError("`git` was not found on PATH. Drift requires git.") from exc
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout.strip()


def repo_root() -> Path:
    """Absolute path of the enclosing git repository's top level."""
    try:
        return Path(_git("rev-parse", "--show-toplevel")).resolve()
    except GitError as exc:
        raise GitError(
            "Not inside a git repository. Drift keys every snapshot to a commit "
            "hash, so it must be run from within a git repo."
        ) from exc


def head_hash() -> str:
    """Full 40-character commit hash of HEAD."""
    try:
        return _git("rev-parse", "HEAD")
    except GitError as exc:
        raise GitError(
            "Could not read the current commit (`git rev-parse HEAD`). "
            "Are you inside a git repository with at least one commit?"
        ) from exc


def has_uncommitted_changes() -> bool:
    """True when tracked files differ from HEAD, so HEAD does not describe the tree."""
    return bool(_git("status", "--porcelain", "--untracked-files=no"))
