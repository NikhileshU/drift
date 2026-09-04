"""P9-2 — metric polarity.

Drift assumed every metric was higher-is-better. Angela's aider dogfood (P9-H2)
proved this live: `run-length-encoding`'s `cost` going 0.010 -> 0.070 — a 7x blowup —
classified Improved. `test_run_length_encoding_cost_blowup_is_degraded_not_improved`
below reproduces that exact fixture by name, per the spec's requirement that this
regression test be named and shown failing against the bug before it is shown fixed.
"""

import pytest

from getdrift.diffing import compare, parse_metric_polarity
from getdrift.paths import ConfigError


def _case(case_id, score, metric="cost", passed=True, environment="golden_set"):
    return {
        "case_id": case_id,
        "metric_scores": {metric: score},
        "pass": passed,
        "environment": environment,
        "timestamp": "2026-09-04T09:00:00Z",
    }


# --- the named regression test ------------------------------------------------


def test_run_length_encoding_cost_blowup_is_degraded_not_improved():
    """Angela's real fixture (aider `2860029` -> `5dc9490`, P9-H2's report.md):
    `cost` 0.010 -> 0.070 must classify Degraded once `cost` is declared
    lower_is_better. Shown failing against `main @ d0fce8b` (bucket == "Improved"
    there) before this fix and passing after — see the P9-2 delivery message for
    that before/after output.
    """
    before = {"cases": [_case("run-length-encoding", 0.010)]}
    after = {"cases": [_case("run-length-encoding", 0.070)]}
    diffs, _ = compare(before, after, metric_polarity={"cost": "lower_is_better"})
    assert diffs[0].bucket == "Degraded"
    # The stored/displayed delta stays the real, unsigned number — only the bucket
    # decision reads the polarity-adjusted sign.
    assert diffs[0].delta == pytest.approx(0.060)


# --- diffing: lower_is_better inverts Improved/Degraded, nothing else ----------


def test_lower_is_better_metric_decreasing_is_improved():
    before = {"cases": [_case("c", 0.070)]}
    after = {"cases": [_case("c", 0.010)]}
    diffs, _ = compare(before, after, metric_polarity={"cost": "lower_is_better"})
    assert diffs[0].bucket == "Improved"


def test_lower_is_better_metric_increasing_is_degraded():
    before = {"cases": [_case("c", 0.010)]}
    after = {"cases": [_case("c", 0.070)]}
    diffs, _ = compare(before, after, metric_polarity={"cost": "lower_is_better"})
    assert diffs[0].bucket == "Degraded"


def test_lower_is_better_still_respects_the_threshold():
    """Polarity only flips which direction is "better" — a tiny move must still read
    Unchanged, exactly as it would for a higher_is_better metric."""
    before = {"cases": [_case("c", 0.010)]}
    after = {"cases": [_case("c", 0.012)]}  # +0.002, under DEFAULT_THRESHOLD (0.05)
    diffs, _ = compare(before, after, metric_polarity={"cost": "lower_is_better"})
    assert diffs[0].bucket == "Unchanged"


# --- the regression test that matters most: the default path is untouched -----


def test_unset_metric_still_reads_higher_is_better_default():
    before = {"cases": [_case("c", 0.5, metric="answer_correctness")]}
    after = {"cases": [_case("c", 0.6, metric="answer_correctness")]}
    diffs, _ = compare(before, after)  # no metric_polarity argument at all
    assert diffs[0].bucket == "Improved"


def test_declaring_polarity_for_one_metric_leaves_another_untouched():
    before = {"cases": [_case("c", 0.5, metric="answer_correctness")]}
    after = {"cases": [_case("c", 0.6, metric="answer_correctness")]}
    diffs, _ = compare(before, after, metric_polarity={"cost": "lower_is_better"})
    assert diffs[0].bucket == "Improved"


def test_explicit_higher_is_better_behaves_identically_to_leaving_it_unset():
    before = {"cases": [_case("c", 0.5, metric="answer_correctness")]}
    after = {"cases": [_case("c", 0.6, metric="answer_correctness")]}
    unset, _ = compare(before, after)
    explicit, _ = compare(before, after, metric_polarity={"answer_correctness": "higher_is_better"})
    assert unset[0].bucket == explicit[0].bucket == "Improved"


# --- config validation: fail loud, never guess ---------------------------------


def test_passed_is_rejected_as_a_polarity_target():
    with pytest.raises(ConfigError, match="passed"):
        parse_metric_polarity({"passed": "lower_is_better"})


def test_an_invalid_polarity_value_is_rejected():
    with pytest.raises(ConfigError, match="cost"):
        parse_metric_polarity({"cost": "smaller_is_nicer"})


def test_absent_or_empty_config_is_the_empty_mapping():
    assert parse_metric_polarity(None) == {}
    assert parse_metric_polarity({}) == {}


def test_a_valid_mapping_passes_through_unchanged():
    raw = {"cost": "lower_is_better", "answer_correctness": "higher_is_better"}
    assert parse_metric_polarity(raw) == raw
