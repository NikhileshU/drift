"""P4-D1: `drift ci`. A false negative here means broken evals ship silently."""
import json
import subprocess

import pytest
from typer.testing import CliRunner

from getdrift.cli import app
from tests.test_diffing import DEMO

runner = CliRunner()

CLEAN = json.loads((DEMO / "baseline.json").read_text())


def _write(repo, name, document):
    path = repo / name
    path.write_text(json.dumps(document))
    return path


def _snapshot(repo, document, **flags):
    args = ["snapshot", "--results-file", str(_write(repo, "r.json", document))]
    for key, value in flags.items():
        args += [f"--{key.replace('_', '-')}", value]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo, message):
    (repo / "README.md").write_text(message + "\n")
    subprocess.run(["git", "commit", "-aqm", message], cwd=repo, check=True)


def _pair(repo, candidate, *, baseline=None, judge="rubric@1", judge2=None):
    """A baseline snapshot on `main`, then a second snapshot one commit later."""
    runner.invoke(app, ["init"])
    subprocess.run(["git", "branch", "-M", "main"], cwd=repo, check=True)
    first = _snapshot(repo, baseline or CLEAN, judge_version=judge)
    _commit(repo, "v2")
    second = _snapshot(repo, candidate, judge_version=judge2 or judge)
    return first, second


def _mutate(**by_case):
    doc = json.loads(json.dumps(CLEAN))
    for case in doc["cases"]:
        change = by_case.get(case["case_id"])
        if change:
            case.update(change)
    return doc


REGRESSED = "escalation_tone_angry"
STABLE = "greeting_smoke_test"


# --- the four cases the spec names -------------------------------------------


def test_clean_pass_exits_zero(git_repo):
    first, second = _pair(git_repo, CLEAN)
    result = runner.invoke(app, ["ci", "--baseline", first, "--current", second])
    assert result.exit_code == 0, result.output
    assert "PASS" in result.output


def test_a_regression_fails_the_build_and_names_the_case(git_repo):
    first, second = _pair(git_repo, _mutate(**{REGRESSED: {"pass": False}}))
    result = runner.invoke(app, ["ci", "--baseline", first, "--current", second])
    assert result.exit_code == 1
    assert "FAIL" in result.output
    assert REGRESSED in result.output


def test_a_judge_change_blocks_the_build_with_a_clear_reason(git_repo):
    """A changed rubric must block exactly like a regression: the diff is untrustworthy."""
    first, second = _pair(git_repo, CLEAN, judge="rubric@1", judge2="rubric@2")
    result = runner.invoke(app, ["ci", "--baseline", first, "--current", second])
    assert result.exit_code == 1
    assert "FAIL" in result.output
    assert "rubric@1" in result.output and "rubric@2" in result.output
    # and it must not be silent about *why* — a bare exit code is the failure mode
    assert "not directly comparable" in result.output.lower()


def test_duplicate_case_id_across_environments_fails_loudly_not_silently(git_repo):
    """P6-A1: `drift snapshot` refuses this at write time; simulate a snapshot written
    some other way — a legacy file predating that check, or one written outside
    `drift snapshot` — the only way a duplicate reaches `drift ci` at all."""
    first, second = _pair(git_repo, CLEAN)
    results_path = git_repo / ".drift" / "snapshots" / second / "results.json"
    document = json.loads(results_path.read_text())
    dup = dict(document["cases"][0])
    dup["environment"] = (
        "production_sample" if dup["environment"] == "golden_set" else "golden_set"
    )
    document["cases"].append(dup)
    results_path.write_text(json.dumps(document))

    result = runner.invoke(app, ["ci", "--baseline", first, "--current", second])
    assert result.exit_code == 1
    assert "error:" in result.output
    assert dup["case_id"] in result.output


def test_a_dirty_tree_is_flagged_but_still_gated(git_repo):
    first, second = _pair(git_repo, CLEAN)
    (git_repo / "README.md").write_text("uncommitted\n")
    result = runner.invoke(app, ["ci", "--baseline", first])  # --current defaults to HEAD
    assert result.exit_code == 0, result.output
    assert "uncommitted changes" in result.output
    assert second[:8] in result.output


# --- the table must be in the log, not just an exit code ---------------------


def test_the_bucketed_table_is_printed_before_a_failing_exit(git_repo):
    """A CI log showing only `exit 1` is useless — the spec's requirement (c)."""
    first, second = _pair(git_repo, _mutate(**{REGRESSED: {"pass": False}}))
    result = runner.invoke(app, ["ci", "--baseline", first, "--current", second])
    assert result.exit_code == 1
    assert "Regressed (1)" in result.output
    assert result.output.index("Regressed (1)") < result.output.index("FAIL")


def test_a_judge_mismatch_still_shows_the_numbers(git_repo):
    first, second = _pair(git_repo, CLEAN, judge="rubric@1", judge2="rubric@2")
    result = runner.invoke(app, ["ci", "--baseline", first, "--current", second])
    assert "Scores only, no verdict" in result.output
    assert result.output.index("Scores only") < result.output.index("FAIL")


# --- --fail-on ---------------------------------------------------------------


def test_degraded_alone_passes_the_default_gate(git_repo):
    """A score drop with both runs passing is not a regression under the default."""
    first, second = _pair(git_repo, _mutate(**{STABLE: {"metric_scores":
        {"answer_correctness": 0.30, "citation_precision": 0.30}}}))
    result = runner.invoke(app, ["ci", "--baseline", first, "--current", second])
    assert "Degraded (1)" in result.output
    assert result.exit_code == 0, result.output


def test_degraded_fails_under_fail_on_degraded(git_repo):
    first, second = _pair(git_repo, _mutate(**{STABLE: {"metric_scores":
        {"answer_correctness": 0.30, "citation_precision": 0.30}}}))
    result = runner.invoke(
        app, ["ci", "--baseline", first, "--current", second, "--fail-on", "degraded"]
    )
    assert result.exit_code == 1
    assert STABLE in result.output


def test_fail_on_degraded_still_catches_regressions(git_repo):
    """The stricter mode must be a superset, never a different set."""
    first, second = _pair(git_repo, _mutate(**{REGRESSED: {"pass": False}}))
    result = runner.invoke(
        app, ["ci", "--baseline", first, "--current", second, "--fail-on", "degraded"]
    )
    assert result.exit_code == 1
    assert REGRESSED in result.output


# --- P6-J1: per-metric bucketing must not change a single-metric suite's counts ---


def test_single_metric_suite_bucket_counts_are_unchanged(git_repo):
    """The demo fixture pins every bucket at once — the sharpest regression guard for
    "per-metric bucketing changed nothing about a suite that only ever had one metric
    per case". If per-metric math ever shifts a single-metric case's verdict, this is
    the line that catches it, not a downstream count somewhere else.
    """
    first, second = _pair(git_repo, CLEAN)
    result = runner.invoke(app, ["ci", "--baseline", first, "--current", second])
    assert result.exit_code == 0, result.output
    assert "Regressed 0" in result.output
    assert "Degraded 0" in result.output
    assert "Fixed 0" in result.output
    assert "Improved 0" in result.output
    assert "New 0" in result.output
    assert "Unchanged" in result.output


def test_an_unknown_fail_on_value_is_rejected(git_repo):
    first, second = _pair(git_repo, CLEAN)
    result = runner.invoke(
        app, ["ci", "--baseline", first, "--current", second, "--fail-on", "everything"]
    )
    assert result.exit_code != 0
    assert "PASS" not in result.output


# --- defaults ----------------------------------------------------------------


def test_baseline_defaults_to_the_newest_snapshot_on_the_default_branch(git_repo):
    first, second = _pair(git_repo, _mutate(**{REGRESSED: {"pass": False}}))
    result = runner.invoke(app, ["ci", "--current", second])
    assert result.exit_code == 1
    assert first[:12] in result.output


def test_a_push_build_on_the_default_branch_compares_against_the_previous_snapshot(
    git_repo,
):
    """HEAD is itself on `main` here — the sample workflow's own push path.

    Naively "newest snapshot on the default branch" resolves to the commit under test,
    and the gate would compare a snapshot against itself.
    """
    first, second = _pair(git_repo, _mutate(**{REGRESSED: {"pass": False}}))
    result = runner.invoke(app, ["ci"])  # no --baseline AND no --current
    assert result.exit_code == 1, result.output
    assert f"{first[:12]}" in result.output and f"{second[:12]}" in result.output
    assert "nothing to gate on" not in result.output
    assert REGRESSED in result.output


def test_default_branch_is_configurable(git_repo):
    _pair(git_repo, CLEAN)
    config = git_repo / ".drift" / "config.yaml"
    config.write_text(config.read_text() + '\ndefault_branch: "nope"\n')
    result = runner.invoke(app, ["ci"])
    assert result.exit_code == 1
    assert "nope" in result.output


def test_no_snapshot_on_the_default_branch_is_a_clear_error(git_repo):
    runner.invoke(app, ["init"])
    subprocess.run(["git", "branch", "-M", "main"], cwd=git_repo, check=True)
    result = runner.invoke(app, ["ci"])
    assert result.exit_code == 1
    assert "no snapshot found" in result.output


def test_the_same_snapshot_on_both_sides_is_refused(git_repo):
    first, _ = _pair(git_repo, CLEAN)
    result = runner.invoke(app, ["ci", "--baseline", first, "--current", first])
    assert result.exit_code == 1
    assert "nothing to gate on" in result.output


# --- the case nobody writes a test for ---------------------------------------


def test_an_unrecorded_judge_version_warns_but_does_not_block(git_repo):
    """UNKNOWN is not MISMATCH. Blocking here would break every team pre-adoption."""
    runner.invoke(app, ["init"])
    subprocess.run(["git", "branch", "-M", "main"], cwd=git_repo, check=True)
    first = _snapshot(git_repo, CLEAN)          # no --judge-version
    _commit(git_repo, "v2")
    second = _snapshot(git_repo, CLEAN)
    result = runner.invoke(app, ["ci", "--baseline", first, "--current", second])
    assert result.exit_code == 0, result.output
    assert "unverified" in result.output


def test_a_new_failing_case_is_not_smuggled_past_the_gate(git_repo):
    """A case absent from the baseline buckets as New, which is NOT Regressed.

    It still must not read as a clean pass: the gate says PASS, and the New row
    carrying `FAIL` is the only thing in the log that says otherwise.
    """
    doc = json.loads(json.dumps(CLEAN))
    doc["cases"].append({
        "case_id": "brand_new_and_broken",
        "metric_scores": {"answer_correctness": 0.1},
        "pass": False,
        "environment": "golden_set",
        "timestamp": "2026-09-02T09:41:02Z",
    })
    first, second = _pair(git_repo, doc)
    result = runner.invoke(app, ["ci", "--baseline", first, "--current", second])
    assert "New (1)" in result.output
    assert "brand_new_and_broken" in result.output
    assert result.exit_code == 0, result.output


# --- P6-A4: same case_id, different environment, across the two snapshots ---------


def _single_case(case_id, environment, score, passed):
    return {
        "schema_version": "1.1.0",
        "cases": [{
            "case_id": case_id,
            "metric_scores": {"answer_correctness": score},
            "pass": passed,
            "environment": environment,
            "timestamp": "2026-09-01T09:00:00Z",
        }],
    }


def test_cross_environment_collision_fails_the_gate_unflagged(git_repo):
    """P6-M1 ruling: an unflagged collision fails the build. A green build asserts
    every case was checked, and a case compared across two environments was not —
    the same 'we know these are not comparable' category a judge-version MISMATCH
    already blocks on, just per-case instead of per-snapshot. The case is still
    reported with its real numbers (SUPPRESSED:, not a bare bucket count) — a case
    the gate refuses to certify must not also become invisible."""
    baseline = _single_case("c", "golden_set", 1.0, True)
    candidate = _single_case("c", "production_sample", 0.2, False)
    first, second = _pair(git_repo, candidate, baseline=baseline)
    result = runner.invoke(app, ["ci", "--baseline", first, "--current", second])
    assert result.exit_code == 1, result.output
    assert "FAIL" in result.output
    assert "PASS" not in result.output
    assert "SUPPRESSED:" in result.output
    assert "golden_set" in result.output and "production_sample" in result.output
    assert "--environment" in result.output  # the failure names its own fix


def test_environment_flag_on_the_gate_narrows_before_matching(git_repo):
    baseline = {
        "schema_version": "1.1.0",
        "cases": [
            {"case_id": "c", "metric_scores": {"answer_correctness": 1.0}, "pass": True,
             "environment": "golden_set", "timestamp": "2026-09-01T09:00:00Z"},
            {"case_id": "stable", "metric_scores": {"answer_correctness": 0.5}, "pass": True,
             "environment": "golden_set", "timestamp": "2026-09-01T09:00:00Z"},
        ],
    }
    candidate = {
        "schema_version": "1.1.0",
        "cases": [
            {"case_id": "c", "metric_scores": {"answer_correctness": 0.2}, "pass": False,
             "environment": "production_sample", "timestamp": "2026-09-01T09:00:00Z"},
            {"case_id": "stable", "metric_scores": {"answer_correctness": 0.5}, "pass": True,
             "environment": "golden_set", "timestamp": "2026-09-01T09:00:00Z"},
        ],
    }
    first, second = _pair(git_repo, candidate, baseline=baseline)
    result = runner.invoke(
        app, ["ci", "--baseline", first, "--current", second, "--environment", "golden_set"]
    )
    assert result.exit_code == 0, result.output
    assert "SUPPRESSED:" not in result.output
    assert "PASS" in result.output


def test_a_non_mapping_config_yaml_is_a_clean_error_not_a_traceback(git_repo):
    """P8-X1: the threshold/noise-sigma read in `drift ci` must turn a broken
    config.yaml shape into `error: ...` + exit 1, not an AttributeError escaping from
    `.get()` on whatever read_config handed back."""
    first, second = _pair(git_repo, CLEAN)
    (git_repo / ".drift" / "config.yaml").write_text("- not\n- a\n- mapping\n")

    result = runner.invoke(app, ["ci", "--baseline", first, "--current", second])
    assert result.exit_code == 1
    assert "error:" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)


def test_a_non_mapping_config_yaml_breaks_default_branch_resolution_cleanly(git_repo):
    """Same as above, but for the `default_branch` read specifically — hit only when
    --baseline is omitted, a separate read_config call from the threshold/sigma one."""
    first, second = _pair(git_repo, _mutate(**{REGRESSED: {"pass": False}}))
    (git_repo / ".drift" / "config.yaml").write_text("- not\n- a\n- mapping\n")

    result = runner.invoke(app, ["ci", "--current", second])
    assert result.exit_code == 1
    assert "error:" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
