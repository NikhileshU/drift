"""A guard written BEFORE the noise filter it guards against.

A5d gates Regressed/Improved on a noise floor derived from the stddev of N repeated
runs. There are two ways to combine that floor with the existing raw threshold, and
only one of them is safe:

    max(raw_threshold, noise_floor)     correct
    noise_floor                         silently destroys the single-run path

A case with one run has stddev 0, so its noise floor is 0, so replacing the raw
threshold with the floor turns the test into `delta > 0` — and every 0.001 of
floating-point wiggle becomes Improved or Degraded. Every snapshot written before
noise-aware diffing existed is a single-run case, so the damage is total, silent, and
lands on exactly the legacy path that is supposed to be untouched.

These tests pin the single-run behaviour that must survive A5d. They pass today against
the raw threshold alone. They will keep passing if the floor is combined with `max`,
and they will fail the moment it is combined by replacement — which is the entire
reason they exist before the code does.
"""

import pytest

from getdrift.diffing import DEFAULT_THRESHOLD, compare

TIMESTAMP = "2026-09-01T09:41:02Z"


def _run(cases):
    """A minimal single-run results.json — no `runs` key, i.e. stddev 0, i.e. floor 0."""
    return {
        "schema_version": "1.0.0",
        "cases": [
            {
                "case_id": case_id,
                "metric_scores": {"accuracy": score},
                "pass": passed,
                "environment": "golden_set",
                "timestamp": TIMESTAMP,
            }
            for case_id, score, passed in cases
        ],
    }


def _bucket(before_score, after_score, passed=True, threshold=DEFAULT_THRESHOLD):
    diffs, _ = compare(
        _run([("c", before_score, passed)]), _run([("c", after_score, passed)]), threshold
    )
    return diffs[0].bucket


# Deltas strictly inside the raw threshold. Under `max(raw, floor)` these stay
# Unchanged forever. Under replacement, floor is 0 at N=1 and every one becomes a
# verdict.
@pytest.mark.parametrize("delta", [0.001, 0.004, 0.01, 0.02, 0.049])
def test_sub_threshold_wiggle_is_never_a_verdict(delta):
    assert _bucket(0.700, 0.700 + delta) == "Unchanged"
    assert _bucket(0.700, 0.700 - delta) == "Unchanged"


def test_floating_point_noise_is_never_a_verdict():
    """0.1 + 0.2 != 0.3. A single-run case must not be bucketed on that."""
    assert _bucket(0.1 + 0.2, 0.3) == "Unchanged"


def test_the_raw_threshold_still_bites_at_n_equals_one():
    """The mirror image: a floor must not swallow a real single-run change either."""
    assert _bucket(0.700, 0.800) == "Improved"
    assert _bucket(0.800, 0.700) == "Degraded"


def test_threshold_boundary_is_exclusive_and_stays_that_way():
    """Exactly at the threshold is not past it. Pinned so a floor cannot shift it.

    Scores chosen to be exact in binary floating point (0.5, 0.75, 0.25), so the
    delta really is the threshold rather than a few ULPs over it — 0.7 + 0.05 minus
    0.7 is 0.05000000000000004, which would make this assert about float error
    instead of about the comparison.
    """
    assert _bucket(0.5, 0.75, threshold=0.25) == "Unchanged"
    assert _bucket(0.5, 0.75 + 1e-9, threshold=0.25) == "Improved"


def test_a_custom_threshold_is_still_a_floor_under_the_noise_floor():
    """`max(raw, noise)` must respect a raw threshold raised by config or --threshold."""
    assert _bucket(0.700, 0.900, threshold=0.5) == "Unchanged"
    assert _bucket(0.700, 0.900, threshold=0.1) == "Improved"


def test_pass_flips_are_not_governed_by_the_score_threshold():
    """Fixed/Regressed are pass-driven; no score threshold or floor may suppress them.

    A5d gates the *score* buckets on noise. The pass buckets are gated on majority of
    runs instead (D2), which at N=1 is the single `pass` value — so a single-run pass
    flip must remain a verdict no matter what either threshold does.
    """
    diffs, _ = compare(_run([("c", 0.7, True)]), _run([("c", 0.7, False)]), 10.0)
    assert diffs[0].bucket == "Regressed"
    diffs, _ = compare(_run([("c", 0.7, False)]), _run([("c", 0.7, True)]), 10.0)
    assert diffs[0].bucket == "Fixed"
