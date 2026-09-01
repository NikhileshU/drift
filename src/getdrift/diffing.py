"""Pure bucketing logic for `drift diff`.

Kept free of Typer and rich so it can be tested — and extended with noise-aware
thresholds — without going through the CLI.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from getdrift.schema import PLACEHOLDER

DEFAULT_THRESHOLD = 0.05

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


def _mean(values: List[float]) -> float:
    return sum(values) / len(values)


def _bucket_case(
    before: Optional[Dict[str, Any]], after: Dict[str, Any], threshold: float
) -> CaseDiff:
    scores_after = after["metric_scores"]
    if before is None:
        return CaseDiff(
            case_id=after["case_id"],
            bucket="New",
            pass_before=None,
            pass_after=after["pass"],
            score_before=None,
            score_after=_mean(list(scores_after.values())),
            delta=None,
            shared_metrics=[],
        )

    scores_before = before["metric_scores"]
    # Only metrics present in both runs are comparable; a metric added or dropped
    # between commits would otherwise show up as a score change that never happened.
    shared = sorted(set(scores_before) & set(scores_after))
    mean_before = _mean([scores_before[m] for m in shared]) if shared else None
    mean_after = _mean([scores_after[m] for m in shared]) if shared else None
    delta = None if mean_before is None else mean_after - mean_before

    was, now = before["pass"], after["pass"]
    if not was and now:
        bucket = "Fixed"
    elif was and not now:
        bucket = "Regressed"
    elif was and now and delta is not None and delta > threshold:
        bucket = "Improved"
    elif was and now and delta is not None and delta < -threshold:
        bucket = "Degraded"
    else:
        bucket = "Unchanged"

    return CaseDiff(
        case_id=after["case_id"],
        bucket=bucket,
        pass_before=was,
        pass_after=now,
        score_before=mean_before,
        score_after=mean_after,
        delta=delta,
        shared_metrics=shared,
    )


def compare(
    before: Dict[str, Any],
    after: Dict[str, Any],
    threshold: float = DEFAULT_THRESHOLD,
) -> Tuple[List[CaseDiff], List[str]]:
    """Bucket every case in `after` against `before`.

    Returns the diffs plus the ids of cases that were in `before` and are gone from
    `after`. The spec defines six buckets and "removed" is not one of them, so those
    ids are reported separately rather than invented into a seventh bucket.
    """
    prior = {case["case_id"]: case for case in before["cases"]}
    diffs = [_bucket_case(prior.get(c["case_id"]), c, threshold) for c in after["cases"]]
    current = {case["case_id"] for case in after["cases"]}
    removed = [case_id for case_id in prior if case_id not in current]
    return diffs, removed
