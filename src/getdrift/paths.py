"""Canonical locations of everything Drift stores, all under `.drift/`."""

from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from getdrift.gitutil import repo_root


def drift_dir() -> Path:
    """`.drift/` in the enclosing git repository root. Raises GitError outside a repo."""
    return repo_root() / ".drift"


class ConfigError(RuntimeError):
    """`.drift/config.yaml` parsed to something other than a mapping at the top level.

    Every `read_config()` caller does `.get(...)` on the result — a top-level list,
    string, number, or bool would otherwise reach that `.get` call and raise a bare
    AttributeError somewhere downstream, far from the file that actually caused it. A
    silent `{}` was considered and rejected: it would make `require_judge_version`,
    `default_branch`, and every other setting read through here silently read as
    unset, which hides a real config problem instead of reporting it — the opposite of
    what those settings are for. Raising here instead is safe everywhere it's
    reachable: the pytest plugin's `_auto_diff` and `pytest_sessionfinish` both already
    wrap everything that can call in here (including transitively, through
    `create_snapshot`) in a broad `except Exception`, so this can never break a host
    suite; every CLI command that reads config catches this alongside `GitError` and
    prints it the same way (see snapshot_cmd.py / diff_cmd.py / ci_cmd.py) — P8-X1.
    """


def read_config(drift: Optional[Path] = None) -> Dict[str, Any]:
    """Parsed `.drift/config.yaml`, or {} when it is absent or empty.

    Raises ConfigError when the file exists and parses to something other than a
    mapping (or `null`/empty, which is the ordinary "nothing configured" case and
    still returns {}) — see ConfigError for why that's a raise, not a silent {}.
    """
    base = drift if drift is not None else drift_dir()
    config = base / "config.yaml"
    if not config.is_file():
        return {}
    parsed = yaml.safe_load(config.read_text(encoding="utf-8"))
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ConfigError(
            f"{config} must be a mapping of key: value pairs at the top level, not a "
            f"{type(parsed).__name__}. `drift init` writes an annotated template; "
            "delete the file to fall back to defaults, or fix its shape."
        )
    return parsed
