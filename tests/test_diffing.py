import json
from pathlib import Path

import pytest

from getdrift.diffing import DEFAULT_THRESHOLD, compare

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


def _case(case_id, score, passed):
    return {
        "case_id": case_id,
        "metric_scores": {"answer_correctness": score},
        "pass": passed,
        "environment": "golden_set",
        "timestamp": "2026-09-01T09:41:02Z",
    }
