"""`drift diff` — bucketed comparison of two snapshots."""

from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

from getdrift.commands import fail
from getdrift.diffing import BUCKET_ORDER, DEFAULT_THRESHOLD, CaseDiff, compare
from getdrift.gitutil import GitError
from getdrift.paths import drift_dir, read_config
from getdrift.snapshot import Snapshot, SnapshotError, load_snapshot

BUCKET_STYLE = {
    "Regressed": "bold red",
    "Degraded": "yellow",
    "Fixed": "bold green",
    "Improved": "green",
    "New": "cyan",
    "Unchanged": "dim",
}


def _threshold(drift: Path, override: Optional[float]) -> float:
    if override is not None:
        return override
    value = read_config(drift).get("diff_threshold")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return DEFAULT_THRESHOLD


def _cell(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.3f}"


def _render(console: Console, bucket: str, cases: List[CaseDiff]) -> None:
    style = BUCKET_STYLE[bucket]
    table = Table(
        title=f"{bucket} ({len(cases)})",
        title_style=style,
        title_justify="left",
        header_style="bold",
        border_style=style,
    )
    table.add_column("case_id", overflow="fold")
    table.add_column("pass", justify="center")
    table.add_column("before", justify="right")
    table.add_column("after", justify="right")
    table.add_column("delta", justify="right")
    for case in sorted(cases, key=lambda c: c.case_id):
        before = "—" if case.pass_before is None else ("pass" if case.pass_before else "FAIL")
        after = "pass" if case.pass_after else "FAIL"
        delta = "—" if case.delta is None else f"{case.delta:+.3f}"
        table.add_row(
            case.case_id,
            f"{before} → {after}",
            _cell(case.score_before),
            _cell(case.score_after),
            delta,
            style=style,
        )
    console.print(table)
    console.print()


def diff(
    hash1: str = typer.Argument(..., help="Baseline snapshot commit hash (or a prefix)."),
    hash2: str = typer.Argument(..., help="Candidate snapshot commit hash (or a prefix)."),
    threshold: Optional[float] = typer.Option(
        None,
        "--threshold",
        help=f"Score delta that counts as Improved/Degraded. Defaults to "
        f"`diff_threshold` in .drift/config.yaml, else {DEFAULT_THRESHOLD}.",
    ),
) -> None:
    """Diff two snapshots into Fixed/Regressed/Improved/Degraded/Unchanged/New."""
    try:
        drift = drift_dir()
    except GitError as exc:
        fail(exc)
    try:
        before, after = load_snapshot(hash1, drift), load_snapshot(hash2, drift)
    except SnapshotError as exc:
        fail(exc)
    if before.path == after.path:
        fail(f"both arguments resolve to the same snapshot ({before.path.name})")

    resolved_threshold = _threshold(drift, threshold)
    diffs, removed = compare(before.results, after.results, resolved_threshold)

    console = Console(highlight=False)
    console.print(
        f"\n[bold]{before.path.name[:12]}[/bold] → [bold]{after.path.name[:12]}[/bold]  "
        f"[dim]threshold {resolved_threshold}[/dim]\n"
    )
    counts = {bucket: [c for c in diffs if c.bucket == bucket] for bucket in BUCKET_ORDER}
    for bucket in BUCKET_ORDER:
        if counts[bucket]:
            _render(console, bucket, counts[bucket])

    summary = "  ".join(
        f"[{BUCKET_STYLE[b]}]{b} {len(counts[b])}[/{BUCKET_STYLE[b]}]" for b in BUCKET_ORDER
    )
    console.print(summary)
    if removed:
        console.print(
            f"[dim]{len(removed)} case(s) present in {before.path.name[:12]} and gone from "
            f"{after.path.name[:12]}: {', '.join(sorted(removed))}[/dim]"
        )
