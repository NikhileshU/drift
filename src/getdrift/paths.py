"""Canonical locations of everything Drift stores, all under `.drift/`."""

from pathlib import Path

from getdrift.gitutil import repo_root

DRIFT_DIRNAME = ".drift"


class DriftPaths:
    """Resolved `.drift/` layout for one repository."""

    def __init__(self, root: Path) -> None:
        self.repo_root = root
        self.drift_dir = root / DRIFT_DIRNAME

    @classmethod
    def discover(cls) -> "DriftPaths":
        """Locate `.drift/` relative to the enclosing git repository root."""
        return cls(repo_root())

    @property
    def config_file(self) -> Path:
        return self.drift_dir / "config.yaml"

    @property
    def golden_set_dir(self) -> Path:
        return self.drift_dir / "golden_set"

    @property
    def snapshots_dir(self) -> Path:
        return self.drift_dir / "snapshots"

    def snapshot_dir(self, commit_hash: str) -> Path:
        return self.snapshots_dir / commit_hash

    @property
    def initialized(self) -> bool:
        return self.drift_dir.is_dir()
