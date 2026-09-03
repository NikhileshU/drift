"""P7-D1: nearest-ancestor snapshot resolution, branch-aware.

The spec this primitive was built for assumed trend.py's `_ancestry()` (git rev-list
--all) could be reused. It can't: --all spans every branch, so a snapshot on a sibling
branch that isn't in HEAD's own history could sort as the "nearest" one — a false
baseline. `nearest_ancestor_snapshot` walks `commit`'s own ancestry only.
"""
import subprocess

from typer.testing import CliRunner

from getdrift.cli import app
from getdrift.snapshot import create_snapshot, nearest_ancestor_snapshot
from tests.test_diffing import DEMO

runner = CliRunner()


def _init(repo):
    assert runner.invoke(app, ["init"]).exit_code == 0


def _commit(repo, message):
    (repo / "README.md").write_text(message + "\n")
    subprocess.run(["git", "commit", "-aqm", message], cwd=repo, check=True)


def test_no_snapshot_anywhere_in_ancestry_returns_none(git_repo):
    _init(git_repo)
    assert nearest_ancestor_snapshot() is None


def test_head_own_snapshot_is_never_returned_as_its_own_baseline(git_repo):
    _init(git_repo)
    create_snapshot(DEMO / "baseline.json")
    assert nearest_ancestor_snapshot() is None


def test_returns_the_nearest_ancestor_when_multiple_snapshots_exist(git_repo):
    _init(git_repo)
    first = create_snapshot(DEMO / "baseline.json")
    _commit(git_repo, "v2")
    second = create_snapshot(DEMO / "candidate.json")
    _commit(git_repo, "v3")  # HEAD; never snapshotted

    assert nearest_ancestor_snapshot() == second.commit_hash
    assert nearest_ancestor_snapshot() != first.commit_hash


def test_snapshot_on_sibling_branch_is_not_returned(git_repo):
    """The case that proves this doesn't just reuse trend.py's `_ancestry()` (--all):
    a snapshot that exists only off a sibling branch must never read as a baseline."""
    _init(git_repo)
    subprocess.run(["git", "checkout", "-qb", "side"], cwd=git_repo, check=True)
    _commit(git_repo, "side commit")
    create_snapshot(DEMO / "baseline.json")  # only reachable from `side`

    subprocess.run(["git", "checkout", "-q", "-"], cwd=git_repo, check=True)  # back to main
    _commit(git_repo, "main commit")  # HEAD now has no snapshotted ancestor

    assert nearest_ancestor_snapshot() is None


def test_shallow_clone_in_detached_head_degrades_to_none_without_raising(git_repo, tmp_path, monkeypatch):
    """Detached HEAD and a shallow clone (truncated `git rev-list` output) must
    degrade to "no baseline", never raise — a pytest plugin runs inside someone
    else's test suite and cannot afford to break their run."""
    _init(git_repo)
    create_snapshot(DEMO / "baseline.json")
    _commit(git_repo, "v2")

    shallow = tmp_path / "shallow"
    subprocess.run(
        # file:// forces a real transport clone — a plain path clone ignores --depth
        # and just hardlinks the full history.
        ["git", "clone", "-q", "--depth", "1", f"file://{git_repo}", str(shallow)],
        check=True,
    )
    monkeypatch.chdir(shallow)
    # A fresh clone has no .drift/ of its own — pass the origin's, since only git
    # history truncation is under test here, not .drift/ propagation.
    assert nearest_ancestor_snapshot(drift=git_repo / ".drift") is None
