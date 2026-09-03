"""`drift trend` — one case (or one metric) across the whole snapshot history.

`drift diff` answers "what changed between these two commits". This answers "what has
been happening to this case", which is a different question with different failure
modes: a decline too gradual for any single diff to call a regression, and a case that
alternates between passing and failing rather than having changed once.

Rendering only. Every number here comes from `getdrift.trend`, which in turn buckets
through the same `compare()` that `drift diff` uses.
"""

import dataclasses
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

from getdrift.commands import fail, warn_if_schemas_stale
from getdrift.commands.diff_cmd import BUCKET_STYLE, SUPPRESSED_MARKER
from getdrift.diffing import (
    DEFAULT_NOISE_SIGMA,
    DEFAULT_THRESHOLD,
    ENVIRONMENT_MISMATCH,
    Environment,
    filter_environment,
)
from getdrift.gitutil import GitError
from getdrift.paths import drift_dir
from getdrift.trend import TrendPoint, case_trend, load_history, metric_trend

#: Same yellow as the suppressed-case notes in `drift diff`. A flagged trend is the
#: single most important line on the screen and must not blend into the table.
FLAG_STYLE = "yellow"

_BLOCKS = "▁▂▃▄▅▆▇█"


def _sparkline(points: List[TrendPoint]) -> str:
    """The shape of the series at a glance. A gap renders as a space, not a zero."""
    scores = [p.score for p in points if p.score is not None]
    if not scores:
        return ""
    low, high = min(scores), max(scores)
    span = high - low
    return "".join(
        " "
        if p.score is None
        else _BLOCKS[
            0 if span == 0 else min(
                len(_BLOCKS) - 1, int((p.score - low) / span * (len(_BLOCKS) - 1))
            )
        ]
        for p in points
    )


def _table(points: List[TrendPoint], show_pass: bool) -> Table:
    table = Table(header_style="bold", border_style="dim")
    table.add_column("commit")
    table.add_column("created_at")
    table.add_column("score", justify="right")
    if show_pass:
        table.add_column("pass", justify="center")
    table.add_column("delta", justify="right")
    table.add_column("vs previous")

    previous: Optional[float] = None
    for point in points:
        if not point.present:
            row = [point.commit_hash[:12], point.created_at or "—", "—"]
            if show_pass:
                row.append("—")
            row += ["—", "absent"]
            table.add_row(*row, style="dim")
            continue
        delta = None if previous is None else point.score - previous
        bucket = point.bucket or "—"
        row = [
            point.commit_hash[:12],
            point.created_at or "—",
            f"{point.score:.3f}",
        ]
        if show_pass:
            row.append("pass" if point.passed else "FAIL")
        cell = (
            "[yellow]no verdict[/yellow]"
            if bucket == ENVIRONMENT_MISMATCH
            else f"[{BUCKET_STYLE[bucket]}]{bucket}[/{BUCKET_STYLE[bucket]}]"
            if bucket in BUCKET_STYLE
            else bucket
        )
        row += ["—" if delta is None else f"{delta:+.3f}", cell]
        table.add_row(*row)
        previous = point.score
    return table


def _flags(console: Console, trend) -> None:
    """The flagged lines, above the table, in the same yellow `drift diff` uses.

    Printed before the table on purpose: these are conclusions about the sequence as a
    whole, and a reader who stops after the first line should still get them.
    """
    drift = getattr(trend, "slow_drift", None)
    if drift is not None:
        console.print(
            f"[{FLAG_STYLE}]SLOW DRIFT — declined across {drift.snapshots} consecutive "
            f"snapshots, {drift.first_score:.3f} → {drift.last_score:.3f} "
            f"(total −{drift.total_drop:.3f}), without any single step being called a "
            f"regression. {drift.start_commit[:12]} → {drift.end_commit[:12]}."
            f"[/{FLAG_STYLE}]"
        )
    flip = getattr(trend, "flip_flop", None)
    if flip is not None:
        console.print(
            f"[{FLAG_STYLE}]FLIP-FLOPPING — pass/fail changed {flip.transitions} times "
            f"across this history, at {', '.join(c[:12] for c in flip.at_commits)}. "
            f"This case is unstable rather than changed.[/{FLAG_STYLE}]"
        )
    unstable = getattr(trend, "flip_flopping_cases", [])
    if unstable:
        console.print(
            f"[{FLAG_STYLE}]FLIP-FLOPPING — {len(unstable)} case(s) carrying this metric "
            f"alternate between passing and failing: {', '.join(unstable)}. Averaging "
            f"hides that, so they are named individually.[/{FLAG_STYLE}]"
        )
    if drift is None and flip is None and not unstable:
        console.print("[dim]No slow drift or flip-flopping detected.[/dim]")
    mismatched = [p for p in getattr(trend, "points", []) if p.bucket == ENVIRONMENT_MISMATCH]
    if mismatched:
        # The table already shows "no verdict" at these rows — this is the line that
        # says why, since a narrow table column is not where a reader looks for a
        # reason. Same marker as `drift diff`/`drift ci`: `--environment` fixes it.
        console.print(
            f"[{FLAG_STYLE}]{SUPPRESSED_MARKER} {len(mismatched)} step(s) compared "
            f"cases scored under different environments; no verdict at those steps: "
            f"{', '.join(p.commit_hash[:12] for p in mismatched)}. Pass --environment "
            f"<golden_set|production_sample> to compare only one.[/{FLAG_STYLE}]"
        )


def trend(
    case_id: Optional[str] = typer.Argument(
        None, help="The case to chart. Omit when using --metric."
    ),
    metric: Optional[str] = typer.Option(
        None,
        "--metric",
        help="Chart this metric averaged across every case that carries it, instead "
        "of a single case.",
    ),
    threshold: Optional[float] = typer.Option(
        None,
        "--threshold",
        help=f"Score delta that counts as Improved/Degraded between consecutive "
        f"snapshots. Defaults to `diff_threshold` in .drift/config.yaml, else "
        f"{DEFAULT_THRESHOLD}.",
    ),
    noise_sigma: Optional[float] = typer.Option(
        None,
        "--noise-sigma",
        help=f"Combined standard deviations a change must clear. Defaults to "
        f"`noise_sigma` in .drift/config.yaml, else {DEFAULT_NOISE_SIGMA}.",
    ),
    environment: Optional[Environment] = typer.Option(
        None,
        "--environment",
        help="Chart only cases from this environment, applied to every snapshot in "
        "the history before it is walked. See `drift diff --help` for what happens "
        "without it, applied here one step at a time instead of once.",
    ),
) -> None:
    """Chart one case (or one metric) across every snapshot in the repo."""
    if (case_id is None) == (metric is None):
        fail("give either a case_id or --metric <name>, but not both")

    try:
        drift = drift_dir()
    except GitError as exc:
        fail(exc)
    warn_if_schemas_stale(drift)

    # Imported here rather than at module scope: these resolve config, and diff_cmd
    # owns the precedence rules for both flags.
    from getdrift.commands.diff_cmd import _noise_sigma, _threshold

    resolved_threshold = _threshold(drift, threshold)
    resolved_sigma = _noise_sigma(drift, noise_sigma)

    history = load_history(drift)
    if not history:
        fail("no snapshots in .drift/snapshots/ — run `drift snapshot` first")
    if environment is not None:
        # Filtered per snapshot, before case_trend/metric_trend ever walk the
        # history — the same "before matching by case_id" rule `drift diff` and
        # `drift ci` apply, just one snapshot at a time instead of one pair.
        history = [
            dataclasses.replace(
                snapshot, results=filter_environment(snapshot.results, environment.value)
            )
            for snapshot in history
        ]
    if len(history) < 2:
        fail(
            f"only one snapshot exists ({history[0].commit_hash[:12]}). A trend needs a "
            "history; snapshot at least one more commit."
        )

    if case_id is not None:
        result = case_trend(case_id, history, threshold=resolved_threshold,
                            noise_sigma=resolved_sigma)
        label, show_pass = f"case {case_id}", True
    else:
        result = metric_trend(metric, history, threshold=resolved_threshold,
                              noise_sigma=resolved_sigma)
        label, show_pass = f"metric {metric}", False

    console = Console(highlight=False)
    present = sum(1 for p in result.points if p.present)
    if not present:
        fail(
            f"{label} does not appear in any of the {len(history)} snapshots. "
            "`drift diff` between two of them to see what case_ids exist."
        )

    console.print(
        f"\n[bold]{label}[/bold]  [dim]{present} of {len(history)} snapshots"
        f"[/dim]  {_sparkline(result.points)}\n"
    )
    _flags(console, result)
    console.print()
    console.print(_table(result.points, show_pass))

    if result.undated:
        console.print(
            f"[{FLAG_STYLE}]{len(result.undated)} snapshot(s) have no readable manifest, "
            f"so they have no timestamp to order by and are shown last: "
            f"{', '.join(c[:12] for c in result.undated)}. The order of this history is "
            f"only as trustworthy as those.[/{FLAG_STYLE}]"
        )
