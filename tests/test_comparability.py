"""A6 — judge-version comparability between two snapshots.

The three states matter more than the wording, so these tests assert on the state
and on what the CLI does with it: a known judge change withholds the verdicts, an
unrecorded one warns and keeps them.
"""

import json
import subprocess

import pytest
from typer.testing import CliRunner

from getdrift.cli import app
from getdrift.diffing import EQUAL, MISMATCH, UNKNOWN, judge_comparability
from tests.test_diffing import DEMO

runner = CliRunner()


def _m(judge):
    return {"judge_version": judge}


@pytest.mark.parametrize(
    "before, after, state",
    [
        (_m("rubric-a"), _m("rubric-a"), EQUAL),
        (_m("rubric-a"), _m("rubric-b"), MISMATCH),
        # The bug OPS-7 was filed for: two unflagged snapshots must NOT read as equal.
        (_m("unset"), _m("unset"), UNKNOWN),
        (_m("unset"), _m("rubric-b"), UNKNOWN),
        (_m("rubric-a"), _m("unset"), UNKNOWN),
        # No manifest at all, and a manifest whose field is missing or not a string.
        (None, _m("rubric-b"), UNKNOWN),
        (None, None, UNKNOWN),
        ({}, _m("rubric-b"), UNKNOWN),
        (_m(None), _m("rubric-b"), UNKNOWN),
    ],
)
def test_states(before, after, state):
    assert judge_comparability(before, after).state == state


def test_only_a_known_change_suppresses_verdicts():
    assert judge_comparability(_m("a"), _m("b")).suppresses_verdicts
    assert not judge_comparability(_m("unset"), _m("unset")).suppresses_verdicts
    assert not judge_comparability(_m("a"), _m("a")).suppresses_verdicts


def test_one_sided_names_which_side_is_missing():
    detail = judge_comparability(_m("unset"), _m("rubric-b")).detail
    assert "baseline" in detail and "rubric-b" in detail
    assert "candidate" in judge_comparability(_m("rubric-a"), _m("unset")).detail


def test_mismatch_detail_names_both_versions():
    assert judge_comparability(_m("a"), _m("b")).detail == "judge version changed from a to b"


# --- end to end, on two snapshots written by `drift snapshot` itself ---


def _two_snapshots(repo, before_judge=None, after_judge=None):
    runner.invoke(app, ["init"])
    hashes = []
    for results, judge in ((DEMO / "baseline.json", before_judge), (DEMO / "candidate.json", after_judge)):
        if hashes:
            (repo / "README.md").write_text(f"v{len(hashes) + 1}\n")
            subprocess.run(["git", "commit", "-aqm", "next"], cwd=repo, check=True)
        argv = ["snapshot", "--results-file", str(results)]
        if judge:
            argv += ["--judge-version", judge]
        assert runner.invoke(app, argv).exit_code == 0
        hashes.append(
            subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
            ).stdout.strip()
        )
    return hashes


def test_judge_change_suppresses_the_verdicts(git_repo):
    first, second = _two_snapshots(git_repo, "rubric-2026-08", "rubric-2026-09")
    result = runner.invoke(app, ["diff", first, second])
    assert result.exit_code == 0
    assert "Not directly comparable" in result.output
    assert "rubric-2026-08" in result.output and "rubric-2026-09" in result.output
    # None of the five judge-dependent verdicts may be claimed.
    for bucket in ("Regressed", "Degraded", "Fixed", "Improved", "Unchanged"):
        assert f"{bucket} (" not in result.output
    # ...but the numbers behind them are still shown, and New still stands.
    assert "Scores only, no verdict" in result.output
    assert "New (1)" in result.output
    assert "legacy_fax_number_lookup" in result.output


def test_unrecorded_judge_warns_but_keeps_the_verdicts(git_repo):
    """OPS-7: `unset` vs `unset` is absence of evidence, not evidence of comparability."""
    first, second = _two_snapshots(git_repo)
    result = runner.invoke(app, ["diff", first, second])
    assert result.exit_code == 0
    assert "neither snapshot records a judge version" in result.output
    assert "unverified" in result.output
    assert "Not directly comparable" not in result.output
    for bucket in ("Regressed", "Degraded", "Fixed", "Improved", "New", "Unchanged"):
        assert f"{bucket} (1)" in result.output


def test_same_judge_diffs_exactly_as_before(git_repo):
    first, second = _two_snapshots(git_repo, "rubric-2026-08", "rubric-2026-08")
    result = runner.invoke(app, ["diff", first, second])
    assert "Not directly comparable" not in result.output
    assert "unverified" not in result.output
    for bucket in ("Regressed", "Degraded", "Fixed", "Improved", "New", "Unchanged"):
        assert f"{bucket} (1)" in result.output


def test_missing_manifest_is_unknown_not_a_crash(git_repo):
    """A hand-assembled snapshot directory still diffs; it just cannot be verified."""
    first, second = _two_snapshots(git_repo, "rubric-a", "rubric-b")
    (git_repo / ".drift" / "snapshots" / first / "manifest.json").unlink()
    result = runner.invoke(app, ["diff", first, second])
    assert result.exit_code == 0
    assert "Not directly comparable" not in result.output
    assert "records no judge version" in result.output


def test_header_shows_all_three_provenance_fields(git_repo):
    first, second = _two_snapshots(git_repo, "rubric-a", "rubric-b")
    output = runner.invoke(app, ["diff", first, second]).output
    for field in ("judge_version", "model_version", "prompt_version"):
        assert field in output


def test_placeholder_never_leaks_into_a_mismatch_claim(git_repo):
    """`unset` is not a judge version, so it can never be one side of "changed from X to Y"."""
    first, second = _two_snapshots(git_repo, None, "rubric-b")
    output = runner.invoke(app, ["diff", first, second]).output
    assert "changed from unset" not in output
