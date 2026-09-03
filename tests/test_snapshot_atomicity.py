"""P6-D1: a snapshot must appear atomically — never half-built on disk."""
from pathlib import Path

import pytest
from typer.testing import CliRunner

from getdrift.cli import app
from getdrift.gitutil import head_hash
from getdrift.paths import drift_dir
from getdrift.snapshot import create_snapshot
from tests.test_diffing import DEMO

runner = CliRunner()


def _init(repo):
    assert runner.invoke(app, ["init"]).exit_code == 0


def test_target_directory_never_exists_mid_write(git_repo, monkeypatch):
    """The bug: mkdir-then-write let a concurrent reader see a half-built directory.
    Spy on every write during snapshot creation — the final path must not exist yet."""
    _init(git_repo)
    target = drift_dir() / "snapshots" / head_hash()

    seen_target_exists = []
    real_write_text = Path.write_text

    def spy(self, *args, **kwargs):
        seen_target_exists.append(target.exists())
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", spy)
    snap = create_snapshot(DEMO / "baseline.json")

    assert seen_target_exists, "write_text was never called — nothing was exercised"
    assert not any(seen_target_exists), "target was visible before every file was written"
    assert snap.path == target
    assert target.is_dir()  # published after the fact, by the single os.replace


def test_temp_dir_does_not_survive_a_write_failure(git_repo, monkeypatch):
    """Boundary: the temp directory must never outlive a failed write."""
    _init(git_repo)
    snapshots_dir = drift_dir() / "snapshots"

    calls = {"n": 0}
    real_write_text = Path.write_text

    def flaky(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full (simulated)")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", flaky)
    with pytest.raises(OSError):
        create_snapshot(DEMO / "baseline.json")

    leftovers = list(snapshots_dir.iterdir()) if snapshots_dir.is_dir() else []
    assert leftovers == []


def test_same_commit_collision_still_fails_loudly(git_repo):
    """Boundary: the fix changes visibility timing, not collision behaviour."""
    from getdrift.snapshot import SnapshotExistsError

    _init(git_repo)
    create_snapshot(DEMO / "baseline.json")
    with pytest.raises(SnapshotExistsError):
        create_snapshot(DEMO / "candidate.json")
