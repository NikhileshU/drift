import json
from pathlib import Path

import pytest

from getdrift.diffing import (
    BUCKET_ORDER,
    DEFAULT_THRESHOLD,
    ENVIRONMENT_MISMATCH,
    DuplicateCaseIdError,
    case_index,
    compare,
    filter_environment,
)

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


# --- P6-J1: case_stats used to average metrics on unrelated scales -----------------
#
# Reachable by default, not by exotic config: the pytest plugin always seeds
# `metric_scores["passed"] = 1.0/0.0` and merges any `drift.score.<name>` the suite
# adds on top — "add one richer score, change no test code" is the documented path.
# Mean of [1.0, 87] = 44.0 is exactly what `case_stats` used to hand back as "the"
# score. These fixtures are shaped like that real case, not a contrived one.


def _multi_metric_case(case_id, passed_score, custom_score, passed):
    """Shaped like a pytest-plugin case: a 0/1 `passed` metric plus a 0-100 custom one."""
    return {
        "case_id": case_id,
        "metric_scores": {"passed": passed_score, "confidence": custom_score},
        "pass": passed,
        "environment": "golden_set",
        "timestamp": "2026-09-01T09:41:02Z",
    }


def test_multi_metric_case_never_reaches_a_blended_score():
    """No number derived from averaging `passed` and `confidence` may reach the caller."""
    before = {"cases": [_multi_metric_case("c", 1.0, 80.0, True)]}
    after = {"cases": [_multi_metric_case("c", 1.0, 90.0, True)]}
    diffs, _ = compare(before, after)
    diff = diffs[0]
    # The old bug: mean([1.0, 80.0]) = 40.5, mean([1.0, 90.0]) = 45.5. Neither number,
    # nor any other blend of the two metrics, may show up anywhere on the case.
    blended_before, blended_after = 40.5, 45.5
    assert diff.score_before != pytest.approx(blended_before)
    assert diff.score_after != pytest.approx(blended_after)
    # There is no single correct number for a multi-metric case's top-level score —
    # only its own metrics, each compared to itself, are trustworthy.
    assert diff.score_before is None
    assert diff.score_after is None
    assert diff.delta is None


def test_multi_metric_case_reports_each_metric_against_itself():
    before = {"cases": [_multi_metric_case("c", 1.0, 80.0, True)]}
    after = {"cases": [_multi_metric_case("c", 1.0, 90.0, True)]}
    diffs, _ = compare(before, after)
    per_metric = {m.metric: m for m in diffs[0].per_metric}
    assert set(per_metric) == {"passed", "confidence"}
    assert per_metric["passed"].score_before == pytest.approx(1.0)
    assert per_metric["passed"].score_after == pytest.approx(1.0)
    assert per_metric["passed"].delta == pytest.approx(0.0)
    assert per_metric["confidence"].score_before == pytest.approx(80.0)
    assert per_metric["confidence"].score_after == pytest.approx(90.0)
    assert per_metric["confidence"].delta == pytest.approx(10.0)


def test_worst_verdict_wins_across_a_case_s_metrics():
    """One metric improving must not quiet down another metric's regression."""
    # `passed` steady at 1.0 (Unchanged); `confidence` drops 80 -> 20, comfortably past
    # both the raw threshold and the noise floor (sd 0 on both sides) -> Degraded.
    before = {"cases": [_multi_metric_case("c", 1.0, 80.0, True)]}
    after = {"cases": [_multi_metric_case("c", 1.0, 20.0, True)]}
    diffs, _ = compare(before, after)
    diff = diffs[0]
    assert diff.bucket == "Degraded"
    by_metric = {m.metric: m.bucket for m in diff.per_metric}
    assert by_metric == {"passed": "Unchanged", "confidence": "Degraded"}


def test_a_pass_flip_makes_every_metric_agree_on_fixed_or_regressed():
    """Fixed/Regressed come from the harness's own pass/fail, not from any score."""
    before = {"cases": [_multi_metric_case("c", 0.0, 10.0, False)]}
    after = {"cases": [_multi_metric_case("c", 1.0, 5.0, True)]}
    diffs, _ = compare(before, after)
    diff = diffs[0]
    assert diff.bucket == "Fixed"
    assert {m.bucket for m in diff.per_metric} == {"Fixed"}


def test_single_metric_case_still_uses_the_top_level_fields():
    """The common case is unaffected: one metric, so the old scalar fields are correct."""
    before = {"cases": [_case("c", 0.5, True)]}
    after = {"cases": [_case("c", 0.6, True)]}
    diffs, _ = compare(before, after)
    diff = diffs[0]
    assert diff.score_before == pytest.approx(0.5)
    assert diff.score_after == pytest.approx(0.6)
    assert diff.delta == pytest.approx(0.1)
    assert len(diff.per_metric) == 1
    assert diff.per_metric[0].metric == "answer_correctness"


# --- P6-A4: same case_id, ONE valid instance per snapshot, different environments --
#
# Different problem from P6-A1's: both snapshots are individually valid (case_index
# passes both), but the same case_id was scored in golden_set in one and
# production_sample in the other. compare() matched them anyway and computed a
# confident verdict comparing two things that were never comparable.


def test_cross_environment_case_suppresses_the_verdict_instead_of_a_false_one():
    """The exact bug, reproduced: golden_set 1.0 pass -> production_sample 0.2 fail
    used to read as a confident Degraded. Verified live on main before this fix."""
    before = {"cases": [_case("c", 1.0, True, "golden_set")]}
    after = {"cases": [_case("c", 0.2, False, "production_sample")]}
    diffs, _ = compare(before, after)
    assert diffs[0].bucket == ENVIRONMENT_MISMATCH
    assert diffs[0].bucket not in BUCKET_ORDER  # none of the six is asserted
    assert diffs[0].score_before == pytest.approx(1.0)
    assert diffs[0].score_after == pytest.approx(0.2)
    assert diffs[0].delta == pytest.approx(-0.8)  # real numbers, not dropped
    assert diffs[0].environment_before == "golden_set"
    assert diffs[0].environment_after == "production_sample"


def test_same_environment_comparison_is_unaffected():
    """Regression pin — the most important one on this card: an ordinary same-
    environment diff must compute exactly the verdict it always did."""
    before = {"cases": [_case("c", 1.0, True, "golden_set")]}
    after = {"cases": [_case("c", 0.2, False, "golden_set")]}
    diffs, _ = compare(before, after)
    assert diffs[0].bucket == "Regressed"
    assert diffs[0].delta == pytest.approx(-0.8)


def test_filter_environment_narrows_cases_and_preserves_other_fields():
    results = {
        "schema_version": "1.1.0",
        "cases": [
            _case("a", 0.5, True, "golden_set"),
            _case("b", 0.5, True, "production_sample"),
        ],
    }
    filtered = filter_environment(results, "golden_set")
    assert [c["case_id"] for c in filtered["cases"]] == ["a"]
    assert filtered["schema_version"] == "1.1.0"


def test_filter_environment_none_is_a_no_op():
    results = {"cases": [_case("a", 0.5, True, "golden_set")]}
    assert filter_environment(results, None) is results


def test_filter_environment_before_matching_removes_the_collision():
    """Applying the filter to both sides upstream of compare() is how `--environment`
    actually resolves the bug — not a new suppression path, a narrower input."""
    before = {"cases": [_case("c", 1.0, True, "golden_set")]}
    after = {"cases": [_case("c", 0.2, False, "production_sample")]}
    diffs, removed = compare(
        filter_environment(before, "golden_set"), filter_environment(after, "golden_set")
    )
    assert diffs == []  # nothing on the golden_set side of `after` to match
    assert removed == ["c"]  # c was golden_set in before, absent from the filtered after
