"""P5-A1 — `drift log`: one line per snapshot, newest first, no `ls .drift/snapshots`."""

import json
import subprocess

from typer.testing import CliRunner

from getdrift.cli import app
from tests.test_diffing import DEMO

runner = CliRunner()


def _snapshot(repo, results_path):
    result = runner.invoke(app, ["snapshot", "--results-file", str(results_path)])
    assert result.exit_code == 0, result.output
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()


def _two_snapshots(repo):
    runner.invoke(app, ["init"])
    first = _snapshot(repo, DEMO / "baseline.json")
    subprocess.run(["git", "commit", "-qm", "second commit", "--allow-empty"], cwd=repo, check=True)
    return first, _snapshot(repo, DEMO / "candidate.json")


def test_lists_newest_first(git_repo):
    first, second = _two_snapshots(git_repo)
    result = runner.invoke(app, ["log"])
    assert result.exit_code == 0
    assert result.output.index(second[:12]) < result.output.index(first[:12])


def test_shows_the_commit_subject(git_repo):
    _two_snapshots(git_repo)
    result = runner.invoke(app, ["log"])
    assert "second commit" in result.output


def test_shows_pass_fail_counts(git_repo):
    """`examples/demo/baseline.json` has 6 cases; the fixture proves it's read, not guessed."""
    _two_snapshots(git_repo)
    cases = json.loads((DEMO / "baseline.json").read_text())["cases"]
    passed = sum(1 for c in cases if c["pass"])
    result = runner.invoke(app, ["log"])
    assert f"{passed}/{len(cases)} pass" in result.output


def test_no_snapshots_points_at_drift_snapshot(git_repo):
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["log"])
    assert result.exit_code == 1
    assert "run `drift snapshot` first" in result.output


def test_a_snapshot_whose_commit_is_gone_degrades_its_row_not_the_listing(git_repo):
    """A rebase or a shallow clone can strand a snapshot's commit_hash. One bad row
    must not take down the other nine — the same reasoning as load_history skipping
    an unreadable snapshot rather than aborting the walk."""
    first, second = _two_snapshots(git_repo)
    snapshots = git_repo / ".drift" / "snapshots"
    gone = "deadbeef" * 5
    (snapshots / first).rename(snapshots / gone)
    result = runner.invoke(app, ["log"])
    assert result.exit_code == 0
    assert second[:12] in result.output
    assert gone[:12] in result.output


def test_undated_snapshots_carry_a_marker_that_survives_no_color(git_repo, monkeypatch):
    """Same rule as A7a/P4-A3: a fact nobody may miss carries text, not colour alone."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    _two_snapshots(git_repo)
    snapshots = git_repo / ".drift" / "snapshots"
    victim = sorted(snapshots.iterdir())[0]
    (victim / "manifest.json").unlink()
    result = runner.invoke(app, ["log"])
    assert "\x1b[" not in result.output
    assert "UNDATED:" in result.output
    assert victim.name[:12] in result.output.split("UNDATED:")[1]
