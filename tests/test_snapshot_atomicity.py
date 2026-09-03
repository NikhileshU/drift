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
    base = drift_dir()

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

    for name in ("snapshots", ".tmp"):
        d = base / name
        leftovers = list(d.iterdir()) if d.is_dir() else []
        assert leftovers == [], f"{name}/ has leftovers: {leftovers}"


def test_orphaned_temp_dir_is_invisible_to_history(git_repo):
    """The gap god caught: a fully-written but unpublished temp dir (the permanent
    window — a crash before os.replace, never reaching the `except OSError` cleanup)
    must not read as a real snapshot to load_history() / `drift log`."""
    from getdrift.trend import load_history

    _init(git_repo)
    real = create_snapshot(DEMO / "baseline.json")

    # Simulate the crash window: a temp dir that finished writing but never got
    # os.replace'd into snapshots/. Old bug: this lived inside snapshots/ itself,
    # so load_history's iterdir() found it and load_snapshot loaded it under its
    # directory name as a fake commit hash.
    orphan = drift_dir() / ".tmp" / "orphan-9f8e7d"
    orphan.mkdir(parents=True)
    (orphan / "results.json").write_text((real.path / "results.json").read_text())
    (orphan / "manifest.json").write_text((real.path / "manifest.json").read_text())

    history = load_history()
    assert [s.commit_hash for s in history] == [real.commit_hash]

    # Belt and braces: even if a stray directory ends up inside snapshots/ itself
    # (a manual copy, editor cruft, anything not our own tmp handling), the name
    # filter must still refuse it — this is the real fix, the .tmp/ move is a
    # second line of defence, not a substitute for it.
    junk = drift_dir() / "snapshots" / "not-a-commit-hash"
    junk.mkdir()
    (junk / "results.json").write_text((real.path / "results.json").read_text())
    history = load_history()
    assert [s.commit_hash for s in history] == [real.commit_hash]


def test_same_commit_collision_still_fails_loudly(git_repo):
    """Boundary: the fix changes visibility timing, not collision behaviour."""
    from getdrift.snapshot import SnapshotExistsError

    _init(git_repo)
    create_snapshot(DEMO / "baseline.json")
    with pytest.raises(SnapshotExistsError):
        create_snapshot(DEMO / "candidate.json")
