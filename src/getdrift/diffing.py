"""Pure bucketing logic for `drift diff`.

Kept free of Typer and rich so it can be tested — and extended with noise-aware
thresholds — without going through the CLI.
"""

import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from getdrift.schema import PLACEHOLDER

DEFAULT_THRESHOLD = 0.05

#: How many combined standard deviations a score change must clear before it is called
#: Improved or Degraded. The spec's figure. Raising it suppresses more borderline calls;
#: lowering it lets sampling noise read as regressions.
DEFAULT_NOISE_SIGMA = 2.0

#: Whether two snapshots were graded by the same judge, and so whether a verdict
#: about the difference between their scores means anything.
#:
#: Three states, not two. EQUAL and MISMATCH are the easy ones. UNKNOWN exists
#: because `drift snapshot` writes the literal placeholder when `--judge-version`
#: is omitted, so two unflagged snapshots would otherwise compare EQUAL — passing
#: the comparability check on precisely the case it exists to catch.
#:
#: UNKNOWN is deliberately NOT treated as MISMATCH. A mismatch is positive evidence
#: that the grader changed and earns suppression; an unrecorded judge version is an
#: absence of evidence. Suppressing it would blank the diff for every team that has
#: not adopted the flag, on every run — and a warning that always fires is one
#: nobody reads by the time a real rubric change trips it.
EQUAL, MISMATCH, UNKNOWN = "equal", "mismatch", "unknown"

#: Display order. Regressed leads because it is the one that stops a release.
BUCKET_ORDER = ["Regressed", "Degraded", "Fixed", "Improved", "New", "Unchanged"]


@dataclass
class Comparability:
    """Whether two snapshots' scores can be compared, and how to say so."""

    state: str
    before: Optional[str]
    after: Optional[str]
    detail: str

    @property
    def suppresses_verdicts(self) -> bool:
        """Only a known judge change invalidates the verdicts. See EQUAL/MISMATCH/UNKNOWN."""
        return self.state == MISMATCH


def _recorded_judge(manifest: Optional[Dict[str, Any]]) -> Optional[str]:
    """The snapshot's judge version, or None if it does not really have one.

    None covers all three ways a snapshot can fail to identify its grader: no
    readable manifest at all, no `judge_version` in it, or the placeholder that
    `drift snapshot` writes when the flag is omitted.
    """
    value = manifest.get("judge_version") if manifest else None
    return value if isinstance(value, str) and value != PLACEHOLDER else None


def judge_comparability(
    before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]
) -> Comparability:
    """Classify two manifests as EQUAL, MISMATCH or UNKNOWN on `judge_version`."""
    old, new = _recorded_judge(before), _recorded_judge(after)
    if old is not None and new is not None:
        if old == new:
            return Comparability(EQUAL, old, new, "")
        return Comparability(
            MISMATCH, old, new, f"judge version changed from {old} to {new}"
        )
    if old is None and new is None:
        return Comparability(
            UNKNOWN, old, new,
            "neither snapshot records a judge version, so Drift cannot tell whether "
            "the grader changed between them",
        )
    # One side only. Worth its own sentence rather than folding into the above: a
    # team adopting --judge-version partway through is the common real-world path,
    # and it tends to happen *because* someone touched the rubric.
    missing, other, known = ("baseline", "candidate", new) if old is None else (
        "candidate", "baseline", old
    )
    return Comparability(
        UNKNOWN, old, new,
        f"the {missing} snapshot records no judge version; the {other} reports "
        f"{known} — Drift cannot tell whether the grader changed between them",
    )


@dataclass
class CaseStats:
    """One case reduced to the numbers a verdict is drawn from.

    A case scored once and a case scored N times differ only in `n` and `sd`: a single
    run has a standard deviation of 0, hence a noise floor of 0, hence exactly the
    pre-noise behaviour. That is what keeps every snapshot written before this feature
    existed diffing to the same verdicts.
    """

    scores: List[float]
    mean: Optional[float]
    sd: float
    n: int
    passed: bool
    passes: int


@dataclass
class CaseDiff:
    """One case's fate between two snapshots."""

    case_id: str
    bucket: str
    pass_before: Optional[bool]
    pass_after: bool
    score_before: Optional[float]
    score_after: Optional[float]
    delta: Optional[float]
    shared_metrics: List[str]
    sd_before: float = 0.0
    sd_after: float = 0.0
    runs_before: int = 0
    runs_after: int = 1
    noise_floor: float = 0.0
    #: The score moved past the raw threshold but not past the noise floor. Recorded
    #: rather than dropped: a filtered case is suppressed, never hidden.
    noise_filtered: bool = False
    #: The harness's own `pass` flipped, but the majority across runs did not.
    pass_flip_filtered: bool = False


def _mean(values: List[float]) -> float:
    return sum(values) / len(values)


def _runs_of(case: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The case's runs, treating a case without a `runs` array as a single run.

    This is the whole compatibility story in one function. Everything downstream sees a
    list of runs and never needs to know which shape it came from.
    """
    return case.get("runs") or [case]


def _stats(case: Dict[str, Any], shared: List[str]) -> CaseStats:
    """Mean and standard deviation of the case's per-run scores over `shared` metrics."""
    runs = _runs_of(case)
    scores = []
    for run in runs:
        values = [run["metric_scores"][m] for m in shared if m in run["metric_scores"]]
        # A run that carries none of the shared metrics falls back to the case-level
        # scores, which are required and therefore always complete.
        if not values:
            values = [case["metric_scores"][m] for m in shared if m in case["metric_scores"]]
        if values:
            scores.append(_mean(values))
    passes = sum(1 for run in runs if run["pass"])
    return CaseStats(
        scores=scores,
        mean=_mean(scores) if scores else None,
        # Sample stddev needs two points; one run has no spread to measure, which is
        # the correct answer rather than a missing one.
        sd=statistics.stdev(scores) if len(scores) > 1 else 0.0,
        n=len(runs),
        passed=_majority(passes, len(runs), case["pass"]),
        passes=passes,
    )


def _majority(passes: int, n: int, reported: bool) -> bool:
    """The case passed if most of its runs did; the harness breaks an exact tie.

    At the default N=3 this alone removes the single flaky pass/fail flip from the
    Fixed and Regressed buckets, which is most of what noise-aware diffing is for.
    """
    if passes * 2 == n:
        return reported
    return passes * 2 > n


def _bucket_case(
    before: Optional[Dict[str, Any]],
    after: Dict[str, Any],
    threshold: float,
    noise_sigma: float,
) -> CaseDiff:
    if before is None:
        fresh = _stats(after, sorted(after["metric_scores"]))
        return CaseDiff(
            case_id=after["case_id"],
            bucket="New",
            pass_before=None,
            pass_after=fresh.passed,
            score_before=None,
            score_after=fresh.mean,
            delta=None,
            shared_metrics=[],
            sd_after=fresh.sd,
            runs_after=fresh.n,
        )

    # Only metrics present in both runs are comparable; a metric added or dropped
    # between commits would otherwise show up as a score change that never happened.
    shared = sorted(set(before["metric_scores"]) & set(after["metric_scores"]))
    old, new = _stats(before, shared), _stats(after, shared)
    delta = None if old.mean is None or new.mean is None else new.mean - old.mean

    # The two thresholds are combined with max(), never by replacement. A single-run
    # case has sd 0 and therefore a noise floor of 0; replacing the raw threshold with
    # the floor would turn the test into `delta > 0` and make every rounding wobble a
    # verdict, silently, on every snapshot written before this feature existed.
    noise_floor = noise_sigma * ((old.sd ** 2 + new.sd ** 2) ** 0.5)
    effective = max(threshold, noise_floor)

    was, now = old.passed, new.passed
    noise_filtered = False
    if not was and now:
        bucket = "Fixed"
    elif was and not now:
        bucket = "Regressed"
    elif was and now and delta is not None and delta > effective:
        bucket = "Improved"
    elif was and now and delta is not None and delta < -effective:
        bucket = "Degraded"
    else:
        bucket = "Unchanged"
        # Improved/Degraded require both sides to pass, per the spec's bucket table,
        # so only a both-passing case can have been filtered by the noise floor.
        noise_filtered = (
            was and now and delta is not None and threshold < abs(delta) <= effective
        )

    return CaseDiff(
        case_id=after["case_id"],
        bucket=bucket,
        pass_before=was,
        pass_after=now,
        score_before=old.mean,
        score_after=new.mean,
        delta=delta,
        shared_metrics=shared,
        sd_before=old.sd,
        sd_after=new.sd,
        runs_before=old.n,
        runs_after=new.n,
        noise_floor=noise_floor,
        noise_filtered=noise_filtered,
        pass_flip_filtered=(before["pass"] != after["pass"]) and was == now,
    )


def compare(
    before: Dict[str, Any],
    after: Dict[str, Any],
    threshold: float = DEFAULT_THRESHOLD,
    noise_sigma: float = DEFAULT_NOISE_SIGMA,
) -> Tuple[List[CaseDiff], List[str]]:
    """Bucket every case in `after` against `before`.

    Returns the diffs plus the ids of cases that were in `before` and are gone from
    `after`. The spec defines six buckets and "removed" is not one of them, so those
    ids are reported separately rather than invented into a seventh bucket.
    """
    prior = {case["case_id"]: case for case in before["cases"]}
    diffs = [
        _bucket_case(prior.get(c["case_id"]), c, threshold, noise_sigma)
        for c in after["cases"]
    ]
    current = {case["case_id"] for case in after["cases"]}
    removed = [case_id for case_id in prior if case_id not in current]
    return diffs, removed
