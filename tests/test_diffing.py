import json
from pathlib import Path

import pytest

from getdrift.diffing import DEFAULT_THRESHOLD, DuplicateCaseIdError, case_index, compare

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo"


@pytest.fixture()
def demo():
    return (
        json.loads((DEMO / "baseline.json").read_text()),
        json.loads((DEMO / "candidate.json").read_text()),
    )


def _buckets(diffs):
    return {d.case_id: d.bucket for d in diffs}


def test_every_bucket_is_reachable(demo):
    diffs, removed = compare(*demo)
    assert _buckets(diffs) == {
        "refund_policy_multi_turn": "Fixed",
        "escalation_tone_angry": "Regressed",
        "sku_lookup_ambiguous": "Improved",
        "multi_hop_inventory_question": "Degraded",
        "greeting_smoke_test": "Unchanged",
        "tool_call_retry_on_timeout": "New",
    }
    assert removed == ["legacy_fax_number_lookup"]


def test_fail_to_pass_is_fixed_even_when_the_score_drops():
    """The spec keys Fixed/Regressed off `pass`, not off the score."""
    before = {"cases": [_case("c", 0.9, passed=False)]}
    after = {"cases": [_case("c", 0.1, passed=True)]}
    diffs, _ = compare(before, after)
    assert diffs[0].bucket == "Fixed"


def test_pass_to_fail_is_regressed_even_when_the_score_rises():
    before = {"cases": [_case("c", 0.1, passed=True)]}
    after = {"cases": [_case("c", 0.9, passed=False)]}
    diffs, _ = compare(before, after)
    assert diffs[0].bucket == "Regressed"


@pytest.mark.parametrize(
    "delta,expected",
    [
        (0.2, "Improved"),
        (-0.2, "Degraded"),
        (0.01, "Unchanged"),
        (-0.01, "Unchanged"),
        (DEFAULT_THRESHOLD, "Unchanged"),      # boundary: strictly greater than
        (-DEFAULT_THRESHOLD, "Unchanged"),
        (DEFAULT_THRESHOLD + 1e-9, "Improved"),
    ],
)
def test_threshold_boundaries(delta, expected):
    # Scores are 0.0 -> delta so the delta is exact; 0.5 + 0.05 is not 0.55 in binary.
    before = {"cases": [_case("c", 0.0, passed=True)]}
    after = {"cases": [_case("c", delta, passed=True)]}
    diffs, _ = compare(before, after)
    assert diffs[0].bucket == expected


def test_threshold_is_configurable():
    before = {"cases": [_case("c", 0.5, passed=True)]}
    after = {"cases": [_case("c", 0.6, passed=True)]}
    assert compare(before, after, threshold=0.05)[0][0].bucket == "Improved"
    assert compare(before, after, threshold=0.5)[0][0].bucket == "Unchanged"


def test_both_failing_is_unchanged_whatever_the_score_did():
    """Improved/Degraded require both to pass, per the spec's bucket table."""
    before = {"cases": [_case("c", 0.1, passed=False)]}
    after = {"cases": [_case("c", 0.9, passed=False)]}
    diffs, _ = compare(before, after)
    assert diffs[0].bucket == "Unchanged"
    assert diffs[0].delta == pytest.approx(0.8)


def test_only_metrics_present_in_both_snapshots_are_compared():
    before = {"cases": [{"case_id": "c", "metric_scores": {"a": 0.5, "dropped": 0.0},
                         "pass": True, "environment": "golden_set",
                         "timestamp": "2026-09-01T09:41:02Z"}]}
    after = {"cases": [{"case_id": "c", "metric_scores": {"a": 0.5, "added": 1.0},
                        "pass": True, "environment": "golden_set",
                        "timestamp": "2026-09-01T09:41:02Z"}]}
    diffs, _ = compare(before, after)
    assert diffs[0].shared_metrics == ["a"]
    assert diffs[0].delta == pytest.approx(0.0)
    assert diffs[0].bucket == "Unchanged"


def test_new_case_has_no_delta():
    diffs, _ = compare({"cases": []}, {"cases": [_case("c", 0.7, passed=True)]})
    assert diffs[0].bucket == "New"
    assert diffs[0].delta is None
    assert diffs[0].score_after == pytest.approx(0.7)


def _case(case_id, score, passed, environment="golden_set"):
    return {
        "case_id": case_id,
        "metric_scores": {"answer_correctness": score},
        "pass": passed,
        "environment": environment,
        "timestamp": "2026-09-01T09:41:02Z",
    }


# --- P6-A1: same case_id run in two environments must not silently vanish ---------
#
# `drift snapshot` already refuses a duplicate case_id at write time — these fixtures
# stand in for a snapshot written some other way (a legacy file predating that check,
# or one produced outside `drift snapshot` entirely), which is the only way a
# duplicate reaches `compare()` at all.


def test_duplicate_case_id_in_before_refuses_instead_of_dropping_one():
    before = {
        "cases": [
            _case("c", 0.9, True, "golden_set"),
            _case("c", 0.1, False, "production_sample"),
        ]
    }
    after = {"cases": [_case("c", 0.9, True, "golden_set")]}
    with pytest.raises(DuplicateCaseIdError, match="'c'"):
        compare(before, after)


def test_duplicate_case_id_in_after_also_refuses():
    """Line 279's `current` set used to silently collapse a duplicate on this side
    too — a set membership test hides it just as effectively as the dict did."""
    before = {"cases": [_case("c", 0.9, True, "golden_set")]}
    after = {
        "cases": [
            _case("c", 0.9, True, "golden_set"),
            _case("c", 0.1, False, "production_sample"),
        ]
    }
    with pytest.raises(DuplicateCaseIdError, match="'c'"):
        compare(before, after)


def test_duplicate_case_id_error_names_both_environments():
    cases = [
        _case("dup", 0.5, True, "golden_set"),
        _case("dup", 0.5, True, "production_sample"),
    ]
    with pytest.raises(DuplicateCaseIdError) as excinfo:
        case_index(cases)
    assert "golden_set" in str(excinfo.value)
    assert "production_sample" in str(excinfo.value)


def test_no_duplicates_is_unaffected():
    """The common case — every case_id unique — must keep working exactly as before."""
    cases = [_case("a", 0.5, True), _case("b", 0.6, True)]
    index = case_index(cases)
    assert set(index) == {"a", "b"}
