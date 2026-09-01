"""Pure bucketing logic for `drift diff`.

Kept free of Typer and rich so it can be tested — and extended with noise-aware
thresholds — without going through the CLI.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_THRESHOLD = 0.05

#: Display order. Regressed leads because it is the one that stops a release.
BUCKET_ORDER = ["Regressed", "Degraded", "Fixed", "Improved", "New", "Unchanged"]


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
