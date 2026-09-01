"""A5e — the noisy golden set keeps real regressions and drops sampling noise."""

import json
import sys
from pathlib import Path

import pytest

from getdrift.diffing import compare
from getdrift.schema import validate_results

FIXTURE = Path(__file__).resolve().parent.parent / "examples" / "noisy-golden-set"
sys.path.insert(0, str(FIXTURE))
import generate  # noqa: E402


@pytest.fixture(scope="module")
def snapshots():
    return (
        json.loads((FIXTURE / "baseline.json").read_text()),
        json.loads((FIXTURE / "candidate.json").read_text()),
    )


def test_the_fixture_still_validates(snapshots):
    for document in snapshots:
        validate_results(document)


def test_committed_files_match_the_generator(snapshots):
    """The seed is pinned, so a drifted fixture means someone edited it by hand."""
    assert snapshots == generate.build()


@pytest.mark.parametrize(
    "case_id, expected", [(k, v[3]) for k, v in generate.CASES.items()]
)
def test_noise_aware_verdicts(snapshots, case_id, expected):
    diffs, _ = compare(*snapshots, generate.THRESHOLD, generate.SIGMA)
    assert next(d for d in diffs if d.case_id == case_id).bucket == expected


@pytest.mark.parametrize(
    "case_id, expected", [(k, v[2]) for k, v in generate.CASES.items()]
)
def test_the_old_rule_would_have_said_something_different(snapshots, case_id, expected):
    """Pins the contrast. If both engines agreed everywhere, the fixture proved nothing."""
    before = {c["case_id"]: c for c in snapshots[0]["cases"]}
    after = {c["case_id"]: c for c in snapshots[1]["cases"]}
    assert generate._old_verdict(before[case_id], after[case_id]) == expected


def test_the_filter_changes_four_verdicts_and_three_were_false(snapshots):
    changed = [k for k, v in generate.CASES.items() if v[2] != v[3]]
    assert sorted(changed) == ["flaky-pass", "mixed-n", "noise-swing", "small-drift-noisy"]


def test_real_regressions_are_not_filtered(snapshots):
    diffs, _ = compare(*snapshots, generate.THRESHOLD, generate.SIGMA)
    by_id = {d.case_id: d for d in diffs}
    assert by_id["real-drop-clean"].bucket == "Degraded"
    assert by_id["real-fail"].bucket == "Regressed"
    assert by_id["real-gain-clean"].bucket == "Improved"


def test_the_legacy_case_has_no_noise_floor_at_all(snapshots):
    diffs, _ = compare(*snapshots, generate.THRESHOLD, generate.SIGMA)
    legacy = next(d for d in diffs if d.case_id == "legacy-n1")
    assert legacy.runs_before == 1 and legacy.runs_after == 1
    assert legacy.sd_before == 0.0 and legacy.sd_after == 0.0
    assert legacy.noise_floor == 0.0


def test_a_single_run_baseline_still_gets_a_floor_from_the_noisy_side(snapshots):
    diffs, _ = compare(*snapshots, generate.THRESHOLD, generate.SIGMA)
    mixed = next(d for d in diffs if d.case_id == "mixed-n")
    assert mixed.runs_before == 1 and mixed.runs_after == 3
    assert mixed.noise_floor > generate.THRESHOLD
    assert mixed.bucket == "Unchanged" and mixed.noise_filtered


def test_suppressed_cases_are_flagged_not_hidden(snapshots):
    diffs, _ = compare(*snapshots, generate.THRESHOLD, generate.SIGMA)
    by_id = {d.case_id: d for d in diffs}
    for case_id in ("noise-swing", "small-drift-noisy", "mixed-n"):
        assert by_id[case_id].noise_filtered, case_id
        assert by_id[case_id].delta is not None
    assert by_id["flaky-pass"].pass_flip_filtered
