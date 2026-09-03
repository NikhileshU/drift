"""Pure bucketing logic for `drift diff`.

Kept free of Typer and rich so it can be tested — and extended with noise-aware
thresholds — without going through the CLI.
"""

import statistics
from dataclasses import dataclass, field
from enum import Enum
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

#: A case's `bucket` when the same case_id was scored under two different
#: `environment`s in `before` and `after` — golden_set in one, production_sample in
#: the other. Deliberately not in BUCKET_ORDER: a verdict comparing two different
#: kinds of input is not a verdict about the model, so none of the six is assigned.
#: Every loop that renders BUCKET_ORDER skips a case with this bucket automatically;
#: the caller is responsible for finding and reporting these separately, the same as
#: it already does for "removed" cases.
ENVIRONMENT_MISMATCH = "EnvironmentMismatch"


class Environment(str, Enum):
    """The two values `results.json` allows for a case's `environment` field.

    Kept here rather than duplicated as string literals in every CLI that adds
    `--environment` — `drift diff`, `drift ci`, `drift trend` all import this one.
    """

    golden_set = "golden_set"
    production_sample = "production_sample"


def filter_environment(
    results: Dict[str, Any], environment: Optional[str]
) -> Dict[str, Any]:
    """`results` with only cases from `environment`, or `results` unchanged if None.

    Applied before case_index / compare() ever see the cases: filtering out one
    environment also removes it as a source of cross-environment case_id collisions,
    which is the whole point of `--environment`.
    """
    if environment is None:
        return results
    return {
        **results,
        "cases": [c for c in results.get("cases", []) if c.get("environment") == environment],
    }


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


class DuplicateCaseIdError(ValueError):
    """Two cases in the same results.json share a `case_id`.

    `drift snapshot` already refuses this at write time — `case_id` must be unique
    within a run (schema.py's `_duplicate_case_ids`). Reaching `compare()` with a
    duplicate means the data got here some other way: a snapshot written before that
    check existed, or a results.json produced outside `drift snapshot`. Keying a dict
    comprehension on `case_id` would silently keep one of the two and drop the other
    — the one outcome that is not acceptable — so this refuses instead.
    """


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
class MetricDiff:
    """One metric's own before/after numbers within a case.

    Never averaged with another metric's scale — see `case_stats`'s docstring for why
    that used to happen. `bucket` mirrors the case-level Fixed/Regressed transition
    when the harness's own pass/fail flipped (every metric agrees, because that verdict
    does not come from a score at all), and is this metric's own Improved/Degraded/
    Unchanged call, against its own noise floor, when it did not.
    """

    metric: str
    score_before: Optional[float]
    score_after: Optional[float]
    delta: Optional[float]
    sd_before: float
    sd_after: float
    noise_floor: float
    bucket: str
    noise_filtered: bool = False


#: Worst-verdict-wins precedence when a case's metrics disagree: an alarm must not get
#: quieter because a second metric was calm. Fixed/Regressed never actually compete with
#: the others here — they come from the pass/fail transition, which every metric agrees
#: on — but the order is total so `_worst` never has to special-case an empty gap.
_VERDICT_RANK = {"Regressed": 0, "Degraded": 1, "Unchanged": 2, "Improved": 3, "Fixed": 4}


def _worst(buckets) -> str:
    return min(buckets, key=lambda b: _VERDICT_RANK[b])


@dataclass
class CaseDiff:
    """One case's fate between two snapshots.

    Stays one row per case even when the case carries several metrics — `drift ci`
    counts CASES in the Regressed/Degraded buckets to decide its exit status, and
    splitting a case into one row per metric would change what a bucket count means
    without anyone touching the gate. `score_before`/`score_after`/`delta`/`sd_*`/
    `noise_floor` carry a real, single-metric-comparable number only when the case has
    exactly one shared metric (the common case, and the one every consumer already
    expects); otherwise there is no correct single number and they are None/0.0 rather
    than an average across incompatible scales — see `per_metric` for the real ones.
    """

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
    #: Each metric compared only to itself, before vs after. Always populated, even
    #: for the single-metric case (where it duplicates the top-level fields) — one
    #: place to read a case's numbers from, whether it carries one metric or several.
    per_metric: List[MetricDiff] = field(default_factory=list)
    #: The case's recorded `environment` in each snapshot. Always populated; only
    #: relevant when they differ, which is when `bucket == ENVIRONMENT_MISMATCH`.
    environment_before: Optional[str] = None
    environment_after: Optional[str] = None


def _mean(values: List[float]) -> float:
    return sum(values) / len(values)


def _runs_of(case: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The case's runs, treating a case without a `runs` array as a single run.

    This is the whole compatibility story in one function. Everything downstream sees a
    list of runs and never needs to know which shape it came from.
    """
    return case.get("runs") or [case]


def case_stats(case: Dict[str, Any], metrics: List[str]) -> CaseStats:
    """Mean and standard deviation of the case's per-run scores over `metrics`.

    Public because the trend view reduces a case to exactly these numbers. Two
    implementations of "what this case scored" would eventually disagree, and the
    disagreement would show up as a trend that contradicts the diff.

    `metrics` with more than one entry yields no `mean`/`sd` (`scores` stays empty) —
    P6-J1 fixed every call site that used to average unrelated metrics into one number,
    but the averaging code itself, `_mean(values)` over several metrics' values in one
    run, was still sitting here, correct and meaningless, for whichever future caller
    passed it two metrics without knowing better. `passed`/`passes`/`n` are unaffected:
    they come from each run's own `pass`, never from a score, so a caller that only
    wants those (as `_bucket_case` does for its `New`/pass-flip cases) is unharmed by
    this — only the score-shaped fields refuse to answer a scale-mixing question.
    """
    runs = _runs_of(case)
    scores = []
    if len(metrics) <= 1:
        for run in runs:
            values = [run["metric_scores"][m] for m in metrics if m in run["metric_scores"]]
            # A run that carries none of the requested metrics falls back to the
            # case-level scores, which are required and therefore always complete.
            if not values:
                values = [case["metric_scores"][m] for m in metrics if m in case["metric_scores"]]
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


def case_index(cases: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """`case_id` -> case for one snapshot's `cases` list, or raise on a repeat id.

    Public, and the single place that turns a `cases` array into a lookup — `compare()`
    needs it for both snapshots, and `trend.py` needs the same lookup per snapshot it
    walks. One guard here beats a second one growing independently wherever else a
    case list gets scanned by id, and it means the duplicate check can't fall out of
    sync between them the way it did before this existed.
    """
    index: Dict[str, Dict[str, Any]] = {}
    for case in cases:
        case_id = case["case_id"]
        if case_id in index:
            envs = sorted(
                {index[case_id].get("environment"), case.get("environment")} - {None}
            )
            raise DuplicateCaseIdError(
                f"case_id {case_id!r} appears more than once in one results.json "
                f"(environments: {', '.join(envs) or 'unknown'}) — compare() cannot "
                "tell which one is meant. `drift snapshot` already refuses this; "
                "re-run it, or fix whatever wrote this file directly."
            )
        index[case_id] = case
    return index


def _metric_diff(
    before: Dict[str, Any],
    after: Dict[str, Any],
    metric: str,
    threshold: float,
    noise_sigma: float,
    was: bool,
    now: bool,
) -> MetricDiff:
    """One metric's own verdict, compared only to itself before vs after.

    `was`/`now` come in from the caller rather than being recomputed here: they are
    the harness's pass/fail majority, which does not depend on which metric (or how
    many) you ask `case_stats` about — see its `passed` field. Passing them in means
    every metric in a case agrees on Fixed/Regressed, as they must; only Improved/
    Degraded/Unchanged is this metric's own call.
    """
    old_m, new_m = case_stats(before, [metric]), case_stats(after, [metric])
    delta = None if old_m.mean is None or new_m.mean is None else new_m.mean - old_m.mean
    noise_floor = noise_sigma * ((old_m.sd ** 2 + new_m.sd ** 2) ** 0.5)
    effective = max(threshold, noise_floor)

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

    return MetricDiff(
        metric=metric,
        score_before=old_m.mean,
        score_after=new_m.mean,
        delta=delta,
        sd_before=old_m.sd,
        sd_after=new_m.sd,
        noise_floor=noise_floor,
        bucket=bucket,
        # Only a both-passing, Unchanged metric can have been filtered by the noise
        # floor — Improved/Degraded already cleared it, Fixed/Regressed never checked
        # it. Provably zero outside that case, not just gated on it: if bucket is
        # Improved, delta > effective, so abs(delta) > effective and the `<= effective`
        # below is already false; symmetrically for Degraded.
        noise_filtered=(
            was and now and delta is not None and threshold < abs(delta) <= effective
        ),
    )


def _metric_diff_no_verdict(
    before: Dict[str, Any], after: Dict[str, Any], metric: str
) -> MetricDiff:
    """One metric's own before/after numbers with no verdict.

    Used only for an ENVIRONMENT_MISMATCH case: the numbers are real — this metric
    compared only to itself is dimensionally fine regardless of environment — but
    nothing they might otherwise justify (Improved/Degraded/Unchanged, a noise floor)
    applies, because the two sides were never a valid comparison to begin with.
    """
    old_m, new_m = case_stats(before, [metric]), case_stats(after, [metric])
    delta = None if old_m.mean is None or new_m.mean is None else new_m.mean - old_m.mean
    return MetricDiff(
        metric=metric,
        score_before=old_m.mean,
        score_after=new_m.mean,
        delta=delta,
        sd_before=old_m.sd,
        sd_after=new_m.sd,
        noise_floor=0.0,
        bucket=ENVIRONMENT_MISMATCH,
    )


def _bucket_case(
    before: Optional[Dict[str, Any]],
    after: Dict[str, Any],
    threshold: float,
    noise_sigma: float,
) -> CaseDiff:
    if before is None:
        metrics = sorted(after["metric_scores"])
        fresh = case_stats(after, metrics)
        per_metric = [
            MetricDiff(
                metric=m,
                score_before=None,
                score_after=(stats := case_stats(after, [m])).mean,
                delta=None,
                sd_before=0.0,
                sd_after=stats.sd,
                noise_floor=0.0,
                bucket="New",
            )
            for m in metrics
        ]
        solo = per_metric[0] if len(per_metric) == 1 else None
        return CaseDiff(
            case_id=after["case_id"],
            bucket="New",
            pass_before=None,
            pass_after=fresh.passed,
            score_before=None,
            score_after=solo.score_after if solo else None,
            delta=None,
            shared_metrics=[],
            sd_after=solo.sd_after if solo else 0.0,
            runs_after=fresh.n,
            per_metric=per_metric,
            environment_after=after.get("environment"),
        )

    # Only metrics present in both runs are comparable; a metric added or dropped
    # between commits would otherwise show up as a score change that never happened.
    shared = sorted(set(before["metric_scores"]) & set(after["metric_scores"]))
    # `.passed` does not depend on which metrics are asked for — case_stats derives it
    # from each run's own `pass`, never from a score — so any metric list gets the same
    # answer. Kept here, once, rather than inside the per-metric loop.
    old, new = case_stats(before, shared), case_stats(after, shared)
    was, now = old.passed, new.passed

    env_before, env_after = before.get("environment"), after.get("environment")
    if env_before is not None and env_after is not None and env_before != env_after:
        # Same case_id, same as any other match — but scored in different
        # environments, so a delta between them is not evidence about the model. The
        # same reasoning as a judge-version MISMATCH, applied per case instead of to
        # the whole snapshot: report the real numbers, assign no bucket, and skip
        # per-metric verdict math entirely — there is no valid comparison here for any
        # metric to be Improved/Degraded/Unchanged about.
        per_metric = [_metric_diff_no_verdict(before, after, m) for m in shared]
        solo = per_metric[0] if len(per_metric) == 1 else None
        return CaseDiff(
            case_id=after["case_id"],
            bucket=ENVIRONMENT_MISMATCH,
            pass_before=was,
            pass_after=now,
            score_before=solo.score_before if solo else None,
            score_after=solo.score_after if solo else None,
            delta=solo.delta if solo else None,
            shared_metrics=shared,
            sd_before=solo.sd_before if solo else 0.0,
            sd_after=solo.sd_after if solo else 0.0,
            runs_before=old.n,
            runs_after=new.n,
            environment_before=env_before,
            environment_after=env_after,
            per_metric=per_metric,
        )

    per_metric = [
        _metric_diff(before, after, m, threshold, noise_sigma, was, now) for m in shared
    ]
    solo = per_metric[0] if len(per_metric) == 1 else None

    if per_metric:
        bucket = _worst(d.bucket for d in per_metric)
    elif not was and now:
        bucket = "Fixed"
    elif was and not now:
        bucket = "Regressed"
    else:
        bucket = "Unchanged"

    return CaseDiff(
        case_id=after["case_id"],
        bucket=bucket,
        pass_before=was,
        pass_after=now,
        score_before=solo.score_before if solo else None,
        score_after=solo.score_after if solo else None,
        delta=solo.delta if solo else None,
        shared_metrics=shared,
        sd_before=solo.sd_before if solo else 0.0,
        sd_after=solo.sd_after if solo else 0.0,
        runs_before=old.n,
        runs_after=new.n,
        noise_floor=solo.noise_floor if solo else 0.0,
        noise_filtered=any(d.noise_filtered for d in per_metric),
        pass_flip_filtered=(before["pass"] != after["pass"]) and was == now,
        environment_before=env_before,
        environment_after=env_after,
        per_metric=per_metric,
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

    Raises DuplicateCaseIdError if either snapshot repeats a `case_id` — most likely
    the same case run in two `environment`s. Silently keeping one and dropping the
    other is not a fixable-later ambiguity, so both `before` and `after` are checked,
    not just the one the old code happened to build a dict from.

    A case matched by `case_id` across the two snapshots but scored under different
    `environment`s gets `bucket == ENVIRONMENT_MISMATCH` instead of one of the six —
    a different problem from the duplicate above (one case_id, two valid snapshots,
    an invalid comparison), and not something `--environment` narrows away on its own
    unless the caller applies `filter_environment` to both sides first.
    """
    prior = case_index(before["cases"])
    current = case_index(after["cases"])
    diffs = [
        _bucket_case(prior.get(c["case_id"]), c, threshold, noise_sigma)
        for c in after["cases"]
    ]
    removed = [case_id for case_id in prior if case_id not in current]
    return diffs, removed
