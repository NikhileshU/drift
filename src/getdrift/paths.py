"""Canonical locations of everything Drift stores, all under `.drift/`."""

from pathlib import Path

from getdrift.gitutil import repo_root


def drift_dir() -> Path:
    """`.drift/` in the enclosing git repository root. Raises GitError outside a repo."""
    return repo_root() / ".drift"
