"""Trend across a case's whole snapshot history — the regressions pairwise diffing cannot see.

`drift diff` compares two snapshots. Two failure modes are invisible to it by
construction, no matter how good its thresholds are:

* **Slow drift.** A case loses a little quality at every commit, never enough for any
  single step to clear the regression threshold. Every diff along the way says
  Unchanged, correctly, and the case is materially worse than it was ten commits ago.
* **Flip-flopping.** A case alternates between passing and failing across the history.
  Any one diff sees a normal Fixed or Regressed; only the sequence shows that the case
  is unstable rather than that it changed.

Both are properties of a *sequence*, so seeing them requires walking the whole history.
This module is the pure data layer for that: no CLI, no rendering.

Bucketing is not reimplemented here. Every consecutive pair goes through the existing
`compare()`, so the trend's per-step verdicts are the same verdicts `drift diff` would
print for that pair, computed by the same code. A second implementation would eventually
disagree with the first, and a trend that contradicts the diff is worse than no trend.
"""

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from getdrift.diffing import (
    DEFAULT_NOISE_SIGMA,
    DEFAULT_THRESHOLD,
    case_index,
    case_stats,
    compare,
)
from getdrift.paths import drift_dir
from getdrift.snapshot import Snapshot, load_snapshot

#: Consecutive declining snapshots before a slow drift is worth reporting. Three points
#: is two steps: the smallest sequence that is a trend rather than a single change.
MIN_DRIFT_RUN = 3

#: Pass/fail transitions before a case counts as unstable. Two is the smallest number
#: that can only be alternation — one transition is an ordinary Fixed or Regressed.
MIN_FLIPS = 2


@dataclass
class TrendPoint:
    """One case at one snapshot, plus how it moved from the snapshot before it."""

    commit_hash: str
    created_at: Optional[str]
    score: Optional[float]
    passed: Optional[bool]
    #: Verdict against the previous snapshot, straight from `compare()`. None for the
    #: first point in the history, and for any point where the case was absent.
    bucket: Optional[str]
    #: False when the case does not appear in this snapshot at all. A gap is not a
    #: failure and must not be read as one.
    present: bool = True


@dataclass
class SlowDrift:
    """A monotonic decline no single diff along the way called a regression."""

    start_commit: str
    end_commit: str
    snapshots: int
    first_score: float
    last_score: float

    @property
    def total_drop(self) -> float:
        return self.first_score - self.last_score


@dataclass
class FlipFlop:
    """A case alternating between passing and failing."""

    transitions: int
    #: The commits at which the verdict changed, in order.
    at_commits: List[str] = field(default_factory=list)


@dataclass
class CaseTrend:
    """The full history of one case, with the sequence-level patterns flagged.

    `points` and `per_metric` are never both populated. A case with exactly one
    metric — the common case — uses `points`, unchanged from before per-metric
    diffing existed. A case with several uses `per_metric` instead: one real,
    same-scale `TrendPoint` series per metric, because there is no single correct
    score to put in `points[i].score` for a case whose metrics are on different
    scales — see `case_stats`'s docstring for why averaging them was wrong.
    """

    case_id: str
    points: List[TrendPoint]
    slow_drift: Optional[SlowDrift] = None
    flip_flop: Optional[FlipFlop] = None
    #: Snapshots whose manifest could not be read, so they have no `created_at` to
    #: order by. Ordered last, by commit hash, and named so a caller can say so.
    undated: List[str] = field(default_factory=list)
    #: Populated instead of `points` for a multi-metric case. Keyed by metric name.
    per_metric: Dict[str, List[TrendPoint]] = field(default_factory=dict)
    #: Slow drift is score-based, so a multi-metric case gets one verdict per metric
    #: here instead of the single `slow_drift` above.
    metric_slow_drift: Dict[str, Optional[SlowDrift]] = field(default_factory=dict)

    @property
    def flagged(self) -> bool:
        return (
            self.slow_drift is not None
            or self.flip_flop is not None
            or any(drift is not None for drift in self.metric_slow_drift.values())
        )


@dataclass
class MetricTrend:
    """One metric averaged across every case that carries it, snapshot by snapshot."""

    metric: str
    points: List[TrendPoint]
    slow_drift: Optional[SlowDrift] = None
    #: Flip-flopping is a property of an individual case, not of an average, so it is
    #: reported per case rather than folded into the aggregate series.
    flip_flopping_cases: List[str] = field(default_factory=list)
    undated: List[str] = field(default_factory=list)

    @property
    def flagged(self) -> bool:
        return self.slow_drift is not None or bool(self.flip_flopping_cases)


def _ancestry(base: Path) -> Dict[str, int]:
    """Each commit's position in the repo's history, oldest first.

    Needed as a tiebreak because `created_at` is written to whole-second precision:
    any two snapshots taken in the same second — routine in CI, and trivial in a
    scripted run — compare equal on it. Falling back to commit hash there orders them
    effectively at random, and a randomly ordered history invents slow drifts and
    flip-flops that never happened. Commit ancestry is the only real answer to which
    of two snapshots came first.
    """
    try:
        output = subprocess.run(
            ["git", "rev-list", "--topo-order", "--all"],
            cwd=base.parent,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return {}
    # rev-list is newest first; invert so older commits sort earlier.
    return {commit: len(output) - index for index, commit in enumerate(output)}


def load_history(drift: Optional[Path] = None) -> List[Snapshot]:
    """Every snapshot in the repo, oldest first by its manifest's `created_at`.

    Ordered by `created_at` rather than by commit hash because hashes have no order,
    and rather than by directory mtime because that changes when files are copied.
    Ties on `created_at` — same-second snapshots — are broken by commit ancestry; see
    `_ancestry`. A snapshot whose manifest cannot be read has no timestamp at all and
    is placed last, so it cannot silently land in the middle and distort the sequence.
    """
    base = drift if drift is not None else drift_dir()
    snapshots = base / "snapshots"
    if not snapshots.is_dir():
        return []
    loaded = []
    for path in snapshots.iterdir():
        if not path.is_dir():
            continue
        try:
            loaded.append(load_snapshot(path.name, base))
        except Exception:
            # An unreadable snapshot directory is skipped rather than aborting the
            # whole history: one bad directory should not hide nine good ones.
            continue
    ancestry = _ancestry(base)
    return sorted(loaded, key=lambda s: _ordering_key(s, ancestry))


def _ordering_key(snapshot: Snapshot, ancestry: Optional[Dict[str, int]] = None):
    created = (snapshot.manifest or {}).get("created_at")
    position = (ancestry or {}).get(snapshot.commit_hash, -1)
    return (created is None, created or "", position, snapshot.commit_hash)


def _case_of(snapshot: Snapshot, case_id: str) -> Optional[Dict[str, Any]]:
    """The one case in this snapshot with `case_id`.

    Goes through `case_index` rather than a first-match scan: a `next()` over a
    duplicate `case_id` would quietly return whichever copy came first and hide the
    other from the whole trend — the same drop `compare()` used to make, just walked
    one snapshot at a time. `case_index` raises instead, same as `compare()` does.
    """
    return case_index(snapshot.results.get("cases", [])).get(case_id)


def _buckets_between(
    previous: Snapshot, current: Snapshot, threshold: float, noise_sigma: float
) -> Dict[str, str]:
    """Every case's verdict for one consecutive pair, via the real diff engine."""
    diffs, _ = compare(previous.results, current.results, threshold, noise_sigma)
    return {diff.case_id: diff.bucket for diff in diffs}


def _detect_slow_drift(points: Sequence[TrendPoint], threshold: float) -> Optional[SlowDrift]:
    """The longest run of consecutive snapshots that declined without ever regressing.

    Three conditions, all required:

    1. The score strictly decreases at every step. A flat step is not a decline.
    2. No step in the run was called Degraded or Regressed. If one was, pairwise
       diffing already reported it and there is nothing hidden to surface.
    3. The run's total drop exceeds the raw threshold. Without this a series that
       wobbles down by a thousandth would flag; there is no hidden regression if the
       whole decline is smaller than what a single diff would have called noise.

    Only points where the case is present take part; a snapshot the case is missing
    from ends the run rather than silently bridging across it.
    """
    best: Optional[SlowDrift] = None
    run: List[TrendPoint] = []

    def close(run: List[TrendPoint]) -> Optional[SlowDrift]:
        if len(run) < MIN_DRIFT_RUN:
            return None
        drop = run[0].score - run[-1].score
        if drop <= threshold:
            return None
        return SlowDrift(
            start_commit=run[0].commit_hash,
            end_commit=run[-1].commit_hash,
            snapshots=len(run),
            first_score=run[0].score,
            last_score=run[-1].score,
        )

    for point in points:
        if not point.present or point.score is None:
            best = _longer(best, close(run))
            run = []
            continue
        if run and point.score < run[-1].score and point.bucket not in ("Degraded", "Regressed"):
            run.append(point)
            continue
        best = _longer(best, close(run))
        run = [point]
    return _longer(best, close(run))


def _longer(current: Optional[SlowDrift], candidate: Optional[SlowDrift]) -> Optional[SlowDrift]:
    if candidate is None:
        return current
    if current is None or candidate.snapshots > current.snapshots:
        return candidate
    return current


def _detect_flip_flop(points: Sequence[TrendPoint]) -> Optional[FlipFlop]:
    """Pass/fail alternating two or more times.

    Snapshots the case is absent from are skipped rather than counted as a change:
    a case that was not run did not fail.
    """
    verdicts = [(p.commit_hash, p.passed) for p in points if p.present and p.passed is not None]
    at = [
        commit
        for (_, before), (commit, after) in zip(verdicts, verdicts[1:])
        if before != after
    ]
    return FlipFlop(len(at), at) if len(at) >= MIN_FLIPS else None


def _points_for_case(
    case_id: str, history: Sequence[Snapshot], threshold: float, noise_sigma: float
) -> List[TrendPoint]:
    points = []
    for index, snapshot in enumerate(history):
        case = _case_of(snapshot, case_id)
        bucket = None
        if case is not None and index:
            bucket = _buckets_between(
                history[index - 1], snapshot, threshold, noise_sigma
            ).get(case_id)
        stats = case_stats(case, sorted(case["metric_scores"])) if case else None
        points.append(
            TrendPoint(
                commit_hash=snapshot.commit_hash,
                created_at=(snapshot.manifest or {}).get("created_at"),
                score=stats.mean if stats else None,
                passed=stats.passed if stats else None,
                bucket=bucket,
                present=case is not None,
            )
        )
    return points


def _metrics_of_case(case_id: str, history: Sequence[Snapshot]) -> List[str]:
    """Every metric this case has carried anywhere in its history, sorted.

    Not just the latest snapshot's metrics: an adapter adding a richer score partway
    through history must still be seen as "this case has two metrics", not silently
    treated as single-metric because the union happens to be checked too early.
    """
    metrics = set()
    for snapshot in history:
        case = _case_of(snapshot, case_id)
        if case is not None:
            metrics.update(case["metric_scores"])
    return sorted(metrics)


def _points_for_metric_in_case(
    case_id: str, metric: str, history: Sequence[Snapshot], threshold: float, noise_sigma: float
) -> List[TrendPoint]:
    """One metric's own series for one case — never averaged with the case's other metrics.

    "Present" here means the case carries *this* metric at this snapshot, not merely
    that the case exists: a case can exist without yet carrying a metric added later,
    and that snapshot must read as a gap for this metric's series, not as a real score.
    """
    points = []
    for index, snapshot in enumerate(history):
        case = _case_of(snapshot, case_id)
        has_metric = case is not None and metric in case["metric_scores"]
        bucket = None
        if case is not None and index:
            bucket = _buckets_between(
                history[index - 1], snapshot, threshold, noise_sigma
            ).get(case_id)
        stats = case_stats(case, [metric]) if has_metric else None
        points.append(
            TrendPoint(
                commit_hash=snapshot.commit_hash,
                created_at=(snapshot.manifest or {}).get("created_at"),
                score=stats.mean if stats else None,
                passed=stats.passed if stats else None,
                bucket=bucket,
                present=has_metric,
            )
        )
    return points


def _passfail_points(
    case_id: str, history: Sequence[Snapshot], threshold: float, noise_sigma: float
) -> List[TrendPoint]:
    """Pass/fail and verdict per snapshot, with no score.

    Used only to feed flip-flop detection for a multi-metric case: flip-flopping is a
    property of the harness's own pass/fail, which — like `.passed` on `CaseStats` — does
    not depend on any metric's score, so it needs no per-metric split of its own.
    """
    points = []
    for index, snapshot in enumerate(history):
        case = _case_of(snapshot, case_id)
        bucket = None
        if case is not None and index:
            bucket = _buckets_between(
                history[index - 1], snapshot, threshold, noise_sigma
            ).get(case_id)
        stats = case_stats(case, []) if case is not None else None
        points.append(
            TrendPoint(
                commit_hash=snapshot.commit_hash,
                created_at=(snapshot.manifest or {}).get("created_at"),
                score=None,
                passed=stats.passed if stats else None,
                bucket=bucket,
                present=case is not None,
            )
        )
    return points


def case_trend(
    case_id: str,
    history: Optional[Sequence[Snapshot]] = None,
    drift: Optional[Path] = None,
    threshold: float = DEFAULT_THRESHOLD,
    noise_sigma: float = DEFAULT_NOISE_SIGMA,
) -> CaseTrend:
    """One case across every snapshot, with slow drift and flip-flopping flagged.

    `history` is accepted so callers — and tests — can supply snapshots directly
    instead of going through the filesystem.

    A case carrying more than one metric gets `per_metric` instead of `points` — see
    `CaseTrend`'s docstring for why blending them was wrong. `flip_flop` still comes
    from a single series either way, because it reads only `.passed`, which is the
    same value regardless of which metric (or how many) you compute it through.
    """
    snapshots = list(history) if history is not None else load_history(drift)
    metrics = _metrics_of_case(case_id, snapshots)

    if len(metrics) > 1:
        per_metric = {
            m: _points_for_metric_in_case(case_id, m, snapshots, threshold, noise_sigma)
            for m in metrics
        }
        passfail = _passfail_points(case_id, snapshots, threshold, noise_sigma)
        return CaseTrend(
            case_id=case_id,
            points=[],
            per_metric=per_metric,
            metric_slow_drift={
                m: _detect_slow_drift(pts, threshold) for m, pts in per_metric.items()
            },
            flip_flop=_detect_flip_flop(passfail),
            undated=_undated(snapshots),
        )

    points = _points_for_case(case_id, snapshots, threshold, noise_sigma)
    return CaseTrend(
        case_id=case_id,
        points=points,
        slow_drift=_detect_slow_drift(points, threshold),
        flip_flop=_detect_flip_flop(points),
        undated=_undated(snapshots),
    )


def _undated(history: Sequence[Snapshot]) -> List[str]:
    return [s.commit_hash for s in history if not (s.manifest or {}).get("created_at")]


def metric_trend(
    metric: str,
    history: Optional[Sequence[Snapshot]] = None,
    drift: Optional[Path] = None,
    threshold: float = DEFAULT_THRESHOLD,
    noise_sigma: float = DEFAULT_NOISE_SIGMA,
) -> MetricTrend:
    """One metric averaged over every case carrying it, across the whole history.

    The aggregate has no pass/fail of its own — averaging verdicts would invent a
    number nobody can act on — so its points carry no `passed` or `bucket`, and
    instability is reported as the list of cases that individually flip-flop.
    """
    snapshots = list(history) if history is not None else load_history(drift)
    points, case_ids = [], set()
    for snapshot in snapshots:
        # Through case_index rather than a direct scan of `cases`, same as
        # `_case_of` — a duplicate case_id here used to silently score twice
        # (once under each copy) and skew the average, instead of the case_id
        # collision it actually is.
        cases = case_index(snapshot.results.get("cases", [])).values()
        scoring = [
            case_stats(case, [metric]).mean for case in cases if metric in case["metric_scores"]
        ]
        case_ids.update(case["case_id"] for case in cases if metric in case["metric_scores"])
        present = [value for value in scoring if value is not None]
        points.append(
            TrendPoint(
                commit_hash=snapshot.commit_hash,
                created_at=(snapshot.manifest or {}).get("created_at"),
                score=sum(present) / len(present) if present else None,
                passed=None,
                bucket=None,
                present=bool(present),
            )
        )
    flipping = sorted(
        case_id
        for case_id in case_ids
        if _detect_flip_flop(_points_for_case(case_id, snapshots, threshold, noise_sigma))
    )
    return MetricTrend(
        metric=metric,
        points=points,
        slow_drift=_detect_slow_drift(points, threshold),
        flip_flopping_cases=flipping,
        undated=_undated(snapshots),
    )
