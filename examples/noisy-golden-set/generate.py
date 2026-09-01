#!/usr/bin/env python3
"""Generate (and verify) the deliberately noisy golden set behind A5e.

The point of this fixture is to show that noise-aware diffing keeps real regressions
and drops sampling noise. That only means something if each case is built so the two
engines DISAGREE where it matters — a fixture both engines bucket identically proves
nothing. So every case below is annotated with what the pre-noise rule would have said
and what the noise-aware rule says, and `--verify` asserts both.

Scores are genuinely sampled from a gaussian per run, not hand-typed, so the set is
really non-deterministic; the seed is pinned and the sampled output is committed, so
the test suite that reads it is not.

    python generate.py            # rewrite baseline.json / candidate.json
    python generate.py --verify   # assert the committed files still prove the point
"""

import json
import random
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = 20260901
RUNS = 3
THRESHOLD = 0.05
SIGMA = 2.0
TIMESTAMP = "2026-09-01T09:41:02Z"

#: case_id -> (before, after, expected_old_verdict, expected_new_verdict, what it proves)
#: A side is (mean, sd, passes) where `passes` is how many of the N runs pass, or
#: (score, None, passes) for a single-run case with no `runs` array at all. A fourth
#: element pins the harness's own case-level `pass` when the point of the case is that
#: the harness disagrees with the majority of its runs.
CASES = {
    "real-drop-clean": (
        (0.90, 0.01, 3), (0.60, 0.01, 3),
        "Degraded", "Degraded",
        "a real regression with low variance survives the filter",
    ),
    "noise-swing": (
        # Identical true distributions on both sides: nothing changed at all. Every
        # bit of the observed delta is sampling. The pre-A5 rule still calls it a
        # regression, which is the entire problem A5 exists to fix.
        (0.70, 0.18, 3), (0.70, 0.18, 3),
        "Degraded", "Unchanged",
        "an identically-distributed case still swings past the raw threshold at N=3",
    ),
    "flaky-pass": (
        (0.70, 0.02, 2, True), (0.70, 0.02, 2, False),
        "Regressed", "Unchanged",
        "one flaky run flipping the harness verdict is not a regression",
    ),
    "real-fail": (
        (0.80, 0.02, 3), (0.40, 0.02, 0),
        "Regressed", "Regressed",
        "a case that genuinely stopped passing still reports Regressed",
    ),
    "real-gain-clean": (
        (0.61, 0.01, 3), (0.81, 0.01, 3),
        "Improved", "Improved",
        "a real improvement with low variance survives the filter",
    ),
    "small-drift-noisy": (
        (0.70, 0.11, 3), (0.65, 0.11, 3),
        "Degraded", "Unchanged",
        "a small real drop is NOT separable from noise at N=3 — see the README",
    ),
    "legacy-n1": (
        (0.80, None, 1), (0.70, None, 1),
        "Degraded", "Degraded",
        "a pre-1.1.0 case with no runs array diffs exactly as it always did",
    ),
    "mixed-n": (
        (0.75, None, 1), (0.62, 0.22, 3),
        "Degraded", "Unchanged",
        "a single-run baseline against a noisy candidate takes the floor from the "
        "noisy side, instead of falling back to the raw threshold",
    ),
}


def _sample(rng, mean, sd):
    return round(min(1.0, max(0.0, rng.gauss(mean, sd))), 3)


def _build(side, rng):
    mean, sd, passes = side[:3]
    harness_pass = side[3] if len(side) > 3 else None
    if sd is None:  # single run: no `runs` array at all, the pre-1.1.0 shape
        return {"metric_scores": {"accuracy": mean}, "pass": bool(passes)}
    runs = [
        {"metric_scores": {"accuracy": _sample(rng, mean, sd)}, "pass": index < passes}
        for index in range(RUNS)
    ]
    rng.shuffle(runs)  # so the failing run is not always in the same position
    scores = [r["metric_scores"]["accuracy"] for r in runs]
    return {
        "metric_scores": {"accuracy": round(statistics.fmean(scores), 3)},
        # A harness that reports one sample rather than the majority is exactly what
        # produces the false Regressed on `flaky-pass`. Drift takes the majority.
        "pass": harness_pass if harness_pass is not None else passes * 2 > RUNS,
        "runs": runs,
    }


def build():
    rng = random.Random(SEED)
    before, after = [], []
    for case_id, (old, new, _, _, _) in CASES.items():
        for side, bucket in ((old, before), (new, after)):
            bucket.append({
                "case_id": case_id,
                "environment": "golden_set",
                "timestamp": TIMESTAMP,
                **_build(side, rng),
            })
    wrap = lambda cases: {"schema_version": "1.1.0", "cases": cases,
                          "metadata": {"harness": "drift noisy-golden-set generator",
                                       "seed": SEED}}
    return wrap(before), wrap(after)


def _old_verdict(before, after):
    """The pre-A5 rule: raw threshold on `metric_scores`, harness `pass` as given."""
    delta = after["metric_scores"]["accuracy"] - before["metric_scores"]["accuracy"]
    was, now = before["pass"], after["pass"]
    if not was and now:
        return "Fixed"
    if was and not now:
        return "Regressed"
    if was and now and delta > THRESHOLD:
        return "Improved"
    if was and now and delta < -THRESHOLD:
        return "Degraded"
    return "Unchanged"


def verify(baseline, candidate):
    sys.path.insert(0, str(HERE.parent.parent / "src"))
    from getdrift.diffing import compare

    diffs, _ = compare(baseline, candidate, THRESHOLD, SIGMA)
    old_by_id = {c["case_id"]: c for c in baseline["cases"]}
    new_by_id = {c["case_id"]: c for c in candidate["cases"]}
    failures = []
    for diff in diffs:
        _, _, want_old, want_new, _ = CASES[diff.case_id]
        got_old = _old_verdict(old_by_id[diff.case_id], new_by_id[diff.case_id])
        if got_old != want_old:
            failures.append(f"{diff.case_id}: pre-A5 rule gave {got_old}, expected {want_old}")
        if diff.bucket != want_new:
            failures.append(f"{diff.case_id}: noise-aware gave {diff.bucket}, expected {want_new}")
        print(f"  {diff.case_id:<20} {got_old:<10} -> {diff.bucket:<10} "
              f"delta {diff.delta:+.3f}  floor {diff.noise_floor:.3f}")
    if failures:
        print("\nFIXTURE NO LONGER PROVES ITS POINT:")
        for problem in failures:
            print(f"  {problem}")
        return 1
    print("\nAll 8 cases bucket as designed under both rules.")
    return 0


if __name__ == "__main__":
    if "--verify" in sys.argv:
        raise SystemExit(verify(
            json.loads((HERE / "baseline.json").read_text()),
            json.loads((HERE / "candidate.json").read_text()),
        ))
    baseline, candidate = build()
    for name, doc in (("baseline.json", baseline), ("candidate.json", candidate)):
        (HERE / name).write_text(json.dumps(doc, indent=2) + "\n")
    print(f"wrote baseline.json and candidate.json ({len(CASES)} cases, seed {SEED})")
