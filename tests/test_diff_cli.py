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


# --- P6-A4: same case_id, different environment, across two snapshots -------------


def _case(case_id, environment, score, passed):
    return {
        "case_id": case_id,
        "metric_scores": {"accuracy": score},
        "pass": passed,
        "environment": environment,
        "timestamp": "2026-09-01T09:00:00Z",
    }


def _snapshot_with(repo, cases):
    doc = {"schema_version": "1.1.0", "cases": cases}
    path = repo / "r.json"
    path.write_text(json.dumps(doc))
    return _snapshot(repo, path)


def test_cross_environment_collision_suppresses_and_warns(git_repo):
    """Verified live on main before this fix: `bucket: Degraded | score 1.0 -> 0.2 |
    delta -0.8` — a confident verdict comparing golden_set against production_sample."""
    runner.invoke(app, ["init"])
    first = _snapshot_with(git_repo, [_case("c", "golden_set", 1.0, True)])
    (git_repo / "README.md").write_text("v2\n")
    subprocess.run(["git", "commit", "-aqm", "v2"], cwd=git_repo, check=True)
    second = _snapshot_with(git_repo, [_case("c", "production_sample", 0.2, False)])

    result = runner.invoke(app, ["diff", first, second])
    assert result.exit_code == 0
    assert "Degraded (1)" not in result.output
    assert "Regressed (1)" not in result.output
    assert "Degraded 0" in result.output and "Regressed 0" in result.output
    assert "SUPPRESSED:" in result.output
    assert "golden_set" in result.output and "production_sample" in result.output
    assert "c" in result.output  # the case is still named, not silently dropped
    assert "-0.800" in result.output  # and its real numbers are still shown


def test_cross_environment_warning_survives_no_color(git_repo, monkeypatch):
    """Same discipline as A7a/P4-A3: this must be readable in a CI log."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    runner.invoke(app, ["init"])
    first = _snapshot_with(git_repo, [_case("c", "golden_set", 1.0, True)])
    (git_repo / "README.md").write_text("v2\n")
    subprocess.run(["git", "commit", "-aqm", "v2"], cwd=git_repo, check=True)
    second = _snapshot_with(git_repo, [_case("c", "production_sample", 0.2, False)])

    output = runner.invoke(app, ["diff", first, second]).output
    assert "\x1b[" not in output
    assert "SUPPRESSED:" in output


def test_environment_flag_narrows_before_matching(git_repo):
    """--environment removes the cross-environment case from the comparison entirely
    (it surfaces as a normal removal, not a suppressed verdict) and leaves an
    unrelated same-environment case comparing normally."""
    runner.invoke(app, ["init"])
    first = _snapshot_with(
        git_repo,
        [_case("c", "golden_set", 1.0, True), _case("stable", "golden_set", 0.5, True)],
    )
    (git_repo / "README.md").write_text("v2\n")
    subprocess.run(["git", "commit", "-aqm", "v2"], cwd=git_repo, check=True)
    second = _snapshot_with(
        git_repo,
        [_case("c", "production_sample", 0.2, False), _case("stable", "golden_set", 0.5, True)],
    )

    result = runner.invoke(app, ["diff", first, second, "--environment", "golden_set"])
    assert result.exit_code == 0
    assert "SUPPRESSED:" not in result.output
    assert "Unchanged (1)" in result.output
    assert "REMOVED:" in result.output and "c" in result.output.split("REMOVED:")[1]


def test_environment_flag_does_not_affect_a_same_environment_comparison(git_repo):
    """Regression pin: passing --environment for an ordinary same-environment diff
    must not change a single verdict."""
    runner.invoke(app, ["init"])
    first = _snapshot_with(git_repo, [_case("c", "golden_set", 1.0, True)])
    (git_repo / "README.md").write_text("v2\n")
    subprocess.run(["git", "commit", "-aqm", "v2"], cwd=git_repo, check=True)
    second = _snapshot_with(git_repo, [_case("c", "golden_set", 0.2, False)])

    plain = runner.invoke(app, ["diff", first, second])
    flagged = runner.invoke(app, ["diff", first, second, "--environment", "golden_set"])
    assert plain.exit_code == flagged.exit_code == 0
    assert "Regressed (1)" in plain.output and "Regressed (1)" in flagged.output
    assert "SUPPRESSED:" not in plain.output and "SUPPRESSED:" not in flagged.output


# --- P6-A1: a duplicate case_id must refuse loudly, not vanish silently -----------


def test_duplicate_case_id_across_environments_is_a_clear_error(git_repo):
    """`drift snapshot` refuses this at write time; simulate a snapshot written some
    other way (a legacy file predating that check, or one written outside `drift
    snapshot`) — the only way a duplicate reaches `drift diff` at all."""
    first, second = _two_snapshots(git_repo)
    results_path = git_repo / ".drift" / "snapshots" / second / "results.json"
    document = json.loads(results_path.read_text())
    dup = dict(document["cases"][0])
    dup["environment"] = (
        "production_sample" if dup["environment"] == "golden_set" else "golden_set"
    )
    document["cases"].append(dup)
    results_path.write_text(json.dumps(document))

    result = runner.invoke(app, ["diff", first, second])
    assert result.exit_code == 1
    assert "error:" in result.output
    assert dup["case_id"] in result.output


def test_a_non_mapping_config_yaml_is_a_clean_error_not_a_traceback(git_repo):
    """P8-X1: read_config raises ConfigError for a broken config.yaml shape; `drift
    diff` must turn that into `error: ...` + exit 1, the same as every other config
    problem, not let it escape as a bare AttributeError traceback."""
    first, second = _two_snapshots(git_repo)
    (git_repo / ".drift" / "config.yaml").write_text("- not\n- a\n- mapping\n")

    result = runner.invoke(app, ["diff", first, second])
    assert result.exit_code == 1
    assert "error:" in result.output
    # A clean `fail()` raises typer.Exit, which the runner does not record as an
    # unhandled exception; an escaped AttributeError would show up here instead.
    assert result.exception is None or isinstance(result.exception, SystemExit)
