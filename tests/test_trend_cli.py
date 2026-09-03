"""P4-A2 — `drift trend` rendering, over snapshots written by `drift snapshot` itself."""

import json
import subprocess

import pytest
from typer.testing import CliRunner

from getdrift.cli import app

runner = CliRunner()
YELLOW = "\x1b[33m"

#: A case that loses 0.03 per snapshot — never a regression on its own step — and one
#: that alternates pass/fail without its score moving at all.
DRIFTING = [0.900, 0.870, 0.840, 0.810, 0.780]
FLAKY = [True, False, True, False, True]


def _history(repo, count=5):
    runner.invoke(app, ["init"])
    results = repo / "results.json"
    for index in range(count):
        results.write_text(json.dumps({
            "schema_version": "1.1.0",
            "cases": [
                {"case_id": "drifting", "metric_scores": {"accuracy": DRIFTING[index]},
                 "pass": True, "environment": "golden_set",
                 "timestamp": "2026-09-01T09:00:00Z"},
                {"case_id": "flaky", "metric_scores": {"accuracy": 0.72},
                 "pass": FLAKY[index], "environment": "golden_set",
                 "timestamp": "2026-09-01T09:00:00Z"},
            ],
        }))
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", f"c{index}"],
                       cwd=repo, check=True)
        assert runner.invoke(
            app, ["snapshot", "--results-file", str(results), "--judge-version", "v1"]
        ).exit_code == 0


def test_slow_drift_is_flagged_above_the_table(git_repo):
    _history(git_repo)
    result = runner.invoke(app, ["trend", "drifting"])
    assert result.exit_code == 0
    assert "SLOW DRIFT" in result.output
    assert "0.900" in result.output and "0.780" in result.output
    # Every step was Unchanged; the flag is the only thing that reports the decline.
    assert result.output.count("Unchanged") == 4
    assert result.output.index("SLOW DRIFT") < result.output.index("commit")


def test_the_flag_is_yellow_not_dim(forced_color, git_repo):
    """Same discipline as A7a: a signal must not wear the nothing-happened colour."""
    _history(git_repo)
    result = runner.invoke(app, ["trend", "drifting"], color=True)
    line = next(l for l in result.output.splitlines() if "SLOW DRIFT" in l)
    assert YELLOW in line and "\x1b[2m" not in line


def test_flip_flopping_is_flagged_with_its_transitions(git_repo):
    _history(git_repo)
    result = runner.invoke(app, ["trend", "flaky"])
    assert "FLIP-FLOPPING" in result.output
    assert "4 times" in result.output
    assert "SLOW DRIFT" not in result.output


def test_a_clean_case_says_so_rather_than_staying_silent(git_repo):
    _history(git_repo, count=2)
    result = runner.invoke(app, ["trend", "drifting"])
    assert result.exit_code == 0
    assert "No slow drift or flip-flopping detected" in result.output


def test_metric_mode_averages_and_names_unstable_cases(git_repo):
    _history(git_repo)
    result = runner.invoke(app, ["trend", "--metric", "accuracy"])
    assert result.exit_code == 0
    assert "metric accuracy" in result.output
    assert "flaky" in result.output  # named individually, because averaging hides it
    assert "pass" not in result.output.split("commit")[1].split("\n")[0]


def test_one_row_per_snapshot_with_deltas(git_repo):
    _history(git_repo)
    result = runner.invoke(app, ["trend", "drifting"])
    assert result.output.count("-0.030") == 4
    assert "5 of 5 snapshots" in result.output


@pytest.mark.parametrize("argv", [["trend"], ["trend", "drifting", "--metric", "accuracy"]])
def test_exactly_one_of_case_or_metric_is_required(git_repo, argv):
    _history(git_repo, count=2)
    result = runner.invoke(app, argv)
    assert result.exit_code == 1
    assert "either a case_id or --metric" in result.output


def test_an_unknown_case_names_how_to_find_the_real_ones(git_repo):
    _history(git_repo, count=2)
    result = runner.invoke(app, ["trend", "no-such-case"])
    assert result.exit_code == 1
    assert "does not appear in any of the 2 snapshots" in result.output


def test_a_single_snapshot_is_not_a_trend(git_repo):
    _history(git_repo, count=1)
    result = runner.invoke(app, ["trend", "drifting"])
    assert result.exit_code == 1
    assert "A trend needs a history" in result.output


def test_no_snapshots_at_all_points_at_drift_snapshot(git_repo):
    runner.invoke(app, ["init"])
    result = runner.invoke(app, ["trend", "drifting"])
    assert result.exit_code == 1
    assert "run `drift snapshot` first" in result.output


def test_undated_snapshots_are_surfaced_not_silently_reordered(git_repo):
    """The ordering of the whole history is only as trustworthy as its timestamps."""
    _history(git_repo)
    snapshots = sorted((git_repo / ".drift" / "snapshots").iterdir())
    (snapshots[0] / "manifest.json").unlink()
    result = runner.invoke(app, ["trend", "drifting"])
    assert result.exit_code == 0
    assert "no readable manifest" in result.output
    assert snapshots[0].name[:12] in result.output


def test_the_drift_flags_survive_without_colour(git_repo, monkeypatch):
    """The trend flags already carry literal text, so they need no added marker.

    `SLOW DRIFT` and `FLIP-FLOPPING` are plain characters rather than a colour, which
    is the property the diff notes had to be given. Pinned so it is not lost later.
    """
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    _history(git_repo)
    output = runner.invoke(app, ["trend", "drifting"]).output
    assert "\x1b[" not in output
    assert "SLOW DRIFT" in output


def test_a_successful_trend_exits_zero(git_repo):
    """Exit-code semantics for CI belong to `drift ci`; trend reports, it does not gate."""
    _history(git_repo)
    assert runner.invoke(app, ["trend", "drifting"]).exit_code == 0
    assert runner.invoke(app, ["trend", "flaky"]).exit_code == 0


# --- P6-A4: --environment filters every snapshot before the history is walked -----


def test_environment_flag_narrows_the_history(git_repo):
    """Case "c" is golden_set in snapshots 0 and 2, but production_sample in
    snapshot 1 — one valid instance per snapshot, so this is the P6-A4 collision,
    not the P6-A1 within-run duplicate. Without the flag, the two steps touching
    snapshot 1 read as EnvironmentMismatch rather than a real verdict; with it,
    snapshot 1 has no golden_set case at all and the case is simply absent there."""
    runner.invoke(app, ["init"])
    environments = ["golden_set", "production_sample", "golden_set"]
    for index, environment in enumerate(environments):
        (git_repo / "results.json").write_text(json.dumps({
            "schema_version": "1.1.0",
            "cases": [{
                "case_id": "c", "metric_scores": {"accuracy": 0.9}, "pass": True,
                "environment": environment, "timestamp": "2026-09-01T09:00:00Z",
            }],
        }))
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", f"c{index}"],
                       cwd=git_repo, check=True)
        assert runner.invoke(
            app, ["snapshot", "--results-file", str(git_repo / "results.json"),
                  "--judge-version", "v1"]
        ).exit_code == 0

    unflagged = runner.invoke(app, ["trend", "c"])
    assert unflagged.exit_code == 0
    assert "SUPPRESSED:" in unflagged.output
    assert "no verdict" in unflagged.output
    assert "Regressed" not in unflagged.output and "Degraded" not in unflagged.output

    flagged = runner.invoke(app, ["trend", "c", "--environment", "golden_set"])
    assert flagged.exit_code == 0
    assert "SUPPRESSED:" not in flagged.output
    assert "no verdict" not in flagged.output
    assert "2 of 3 snapshots" in flagged.output  # snapshot 1 has no golden_set case


def test_environment_mismatch_note_survives_no_color(git_repo, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("TERM", "dumb")
    runner.invoke(app, ["init"])
    for index, environment in enumerate(["golden_set", "production_sample"]):
        (git_repo / "results.json").write_text(json.dumps({
            "schema_version": "1.1.0",
            "cases": [{
                "case_id": "c", "metric_scores": {"accuracy": 0.9}, "pass": True,
                "environment": environment, "timestamp": "2026-09-01T09:00:00Z",
            }],
        }))
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", f"c{index}"],
                       cwd=git_repo, check=True)
        assert runner.invoke(
            app, ["snapshot", "--results-file", str(git_repo / "results.json"),
                  "--judge-version", "v1"]
        ).exit_code == 0

    output = runner.invoke(app, ["trend", "c"]).output
    assert "\x1b[" not in output
    assert "SUPPRESSED:" in output


def test_environment_flag_does_not_affect_a_single_environment_history(git_repo):
    """Regression pin: a history that only ever uses one environment must trend
    identically with or without the flag."""
    _history(git_repo)
    plain = runner.invoke(app, ["trend", "drifting"])
    flagged = runner.invoke(app, ["trend", "drifting", "--environment", "golden_set"])
    assert plain.output == flagged.output
