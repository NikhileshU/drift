"""Canonical locations of everything Drift stores, all under `.drift/`."""

from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from getdrift.gitutil import repo_root


def drift_dir() -> Path:
    """`.drift/` in the enclosing git repository root. Raises GitError outside a repo."""
    return repo_root() / ".drift"


def read_config(drift: Optional[Path] = None) -> Dict[str, Any]:
    """Parsed `.drift/config.yaml`, or {} when it is absent or empty."""
    base = drift if drift is not None else drift_dir()
    config = base / "config.yaml"
    if not config.is_file():
        return {}
    return yaml.safe_load(config.read_text(encoding="utf-8")) or {}
