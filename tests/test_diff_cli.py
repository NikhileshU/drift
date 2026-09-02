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
    (repo / "README.md").write_text("v2\n")
    subprocess.run(["git", "commit", "-aqm", "v2"], cwd=repo, check=True)
    return first, _snapshot(repo, DEMO / "candidate.json")


def test_diff_reports_all_six_buckets(git_repo):
    first, second = _two_snapshots(git_repo)
    result = runner.invoke(app, ["diff", first, second])
    assert result.exit_code == 0, result.output
    for bucket in ("Regressed", "Degraded", "Fixed", "Improved", "New", "Unchanged"):
        assert f"{bucket} (1)" in result.output
    assert result.output.index("Regressed") < result.output.index("Fixed")
    assert "legacy_fax_number_lookup" in result.output


def test_diff_accepts_hash_prefixes(git_repo):
    first, second = _two_snapshots(git_repo)
    assert runner.invoke(app, ["diff", first[:7], second[:7]]).exit_code == 0


def test_threshold_flag_changes_bucketing(git_repo):
    first, second = _two_snapshots(git_repo)
    result = runner.invoke(app, ["diff", first, second, "--threshold", "0.5"])
    assert "Unchanged (3)" in result.output
    assert "Improved" not in result.output.split("Unchanged")[0]


def test_threshold_read_from_config(git_repo):
    first, second = _two_snapshots(git_repo)
    config = git_repo / ".drift" / "config.yaml"
    config.write_text(config.read_text() + "\ndiff_threshold: 0.5\n")
    assert "Unchanged (3)" in runner.invoke(app, ["diff", first, second]).output


def test_unknown_hash_is_a_clear_error(git_repo):
    _two_snapshots(git_repo)
    result = runner.invoke(app, ["diff", "deadbeef", "cafe1234"])
    assert result.exit_code == 1
    assert "no snapshot for" in result.output


def test_same_snapshot_twice_is_rejected(git_repo):
    first, _ = _two_snapshots(git_repo)
    result = runner.invoke(app, ["diff", first, first])
    assert result.exit_code == 1
    assert "same snapshot" in result.output


# --- P5-A1: `drift diff` with no args, or one -------------------------------------


def _three_snapshots(repo):
    """Three snapshots on distinct commits, oldest first as written."""
    runner.invoke(app, ["init"])
    first = _snapshot(repo, DEMO / "baseline.json")
    (repo / "README.md").write_text("v2\n")
    subprocess.run(["git", "commit", "-aqm", "v2"], cwd=repo, check=True)
    second = _snapshot(repo, DEMO / "candidate.json")
    (repo / "README.md").write_text("v3\n")
    subprocess.run(["git", "commit", "-aqm", "v3"], cwd=repo, check=True)
    third = _snapshot(repo, DEMO / "baseline.json")
    return first, second, third


def test_no_args_compares_the_two_most_recent_snapshots(git_repo):
    first, second, third = _three_snapshots(git_repo)
    zero_args = runner.invoke(app, ["diff"])
    explicit = runner.invoke(app, ["diff", second, third])
    assert zero_args.exit_code == 0
    assert zero_args.output == explicit.output
    assert first[:12] not in zero_args.output.split("threshold")[0]


def test_one_arg_compares_it_against_the_most_recent(git_repo):
    first, second, third = _three_snapshots(git_repo)
    one_arg = runner.invoke(app, ["diff", first])
    explicit = runner.invoke(app, ["diff", first, third])
    assert one_arg.exit_code == 0
    assert one_arg.output == explicit.output


def test_no_args_with_fewer_than_two_snapshots_is_a_clear_error(git_repo):
    runner.invoke(app, ["init"])
    _snapshot(git_repo, DEMO / "baseline.json")
    result = runner.invoke(app, ["diff"])
    assert result.exit_code == 1
    assert "only 1 snapshot" in result.output
    assert "need at least 2" in result.output


def test_the_explicit_two_hash_form_is_unchanged(git_repo):
    """P5-A1 must not touch the existing behaviour when both hashes are given."""
    first, second, third = _three_snapshots(git_repo)
    result = runner.invoke(app, ["diff", first, second])
    assert result.exit_code == 0
    assert first[:12] in result.output and second[:12] in result.output
    assert third[:12] not in result.output.split("threshold")[0]
