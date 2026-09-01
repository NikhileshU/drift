"""`drift diff` — bucketed comparison of two snapshots."""

import json
from pathlib import Path
from typing import List, Optional

import typer
import yaml
from rich.console import Console
from rich.table import Table

from getdrift.commands import fail
from getdrift.diffing import (
    BUCKET_ORDER,
    DEFAULT_THRESHOLD,
    UNKNOWN,
    CaseDiff,
    Comparability,
    compare,
    judge_comparability,
)
from getdrift.gitutil import GitError
from getdrift.paths import drift_dir
from getdrift.schema import SchemaValidationError, validate_manifest

#: Provenance fields shown in the diff header. Only `judge_version` gates anything;
#: the other two are there because "what else moved?" is the first question a human
#: asks when a diff comes back flagged.
PROVENANCE = ("judge_version", "model_version", "prompt_version")

BUCKET_STYLE = {
    "Regressed": "bold red",
    "Degraded": "yellow",
    "Fixed": "bold green",
    "Improved": "green",
    "New": "cyan",
    "Unchanged": "dim",
}


def _resolve(snapshots: Path, ref: str) -> Path:
    """Accept a full hash or an unambiguous prefix of one."""
    exact = snapshots / ref
    if exact.is_dir():
        return exact
    matches = sorted(p for p in snapshots.glob(f"{ref}*") if p.is_dir())
    if not matches:
        fail(f"no snapshot for {ref!r}. `ls .drift/snapshots` to see what exists.")
    if len(matches) > 1:
        fail(f"{ref!r} matches {len(matches)} snapshots: {', '.join(p.name for p in matches)}")
    return matches[0]


def _load(snapshot: Path) -> dict:
    path = snapshot / "results.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        fail(f"{path} is missing — that snapshot directory is incomplete.")
    except json.JSONDecodeError as exc:
        fail(f"{path} is not valid JSON: {exc}")


def _load_manifest(snapshot: Path, drift: Path) -> Optional[dict]:
    """The snapshot's manifest, or None if it cannot be read or does not conform.

    Deliberately not fatal. A snapshot directory assembled by hand may have no
    manifest; that makes its judge version unknown, which is a state `drift diff`
    already handles, so it is not a reason to refuse to diff at all.
    """
    try:
        document = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
        validate_manifest(document, drift_dir=drift)
        return document
    except (OSError, json.JSONDecodeError, SchemaValidationError):
        return None


def _provenance(
    console: Console, before: Optional[dict], after: Optional[dict]
) -> None:
    for field in PROVENANCE:
        old = (before or {}).get(field) or "—"
        new = (after or {}).get(field) or "—"
        style = "" if old == new else "yellow"
        console.print(f"[dim]{field:<15}[/dim][{style}]{old} → {new}[/{style}]"
                      if style else f"[dim]{field:<15}{old} → {new}[/dim]")


def _flat(console: Console, cases: List[CaseDiff]) -> None:
    """Every case's raw numbers with no bucket column — facts without a verdict."""
    table = Table(
        title=f"Scores only, no verdict ({len(cases)})",
        title_style="bold yellow",
        title_justify="left",
        header_style="bold",
        border_style="yellow",
    )
    table.add_column("case_id", overflow="fold")
    table.add_column("pass", justify="center")
    table.add_column("before", justify="right")
    table.add_column("after", justify="right")
    table.add_column("delta", justify="right")
    for case in sorted(cases, key=lambda c: c.case_id):
        before = "—" if case.pass_before is None else ("pass" if case.pass_before else "FAIL")
        table.add_row(
            case.case_id,
            f"{before} → {'pass' if case.pass_after else 'FAIL'}",
            _cell(case.score_before),
            _cell(case.score_after),
            "—" if case.delta is None else f"{case.delta:+.3f}",
        )
    console.print(table)
    console.print()


def _threshold(drift: Path, override: Optional[float]) -> float:
    if override is not None:
        return override
    config = drift / "config.yaml"
    if config.is_file():
        value = (yaml.safe_load(config.read_text(encoding="utf-8")) or {}).get(
            "diff_threshold"
        )
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
    snapshots = drift / "snapshots"
    if not snapshots.is_dir():
        fail("no .drift/snapshots/ in this repo — run `drift init` first")

    before_dir, after_dir = _resolve(snapshots, hash1), _resolve(snapshots, hash2)
    if before_dir == after_dir:
        fail(f"both arguments resolve to the same snapshot ({before_dir.name})")

    resolved_threshold = _threshold(drift, threshold)
    diffs, removed = compare(_load(before_dir), _load(after_dir), resolved_threshold)
    before_manifest, after_manifest = (
        _load_manifest(before_dir, drift),
        _load_manifest(after_dir, drift),
    )
    comparability = judge_comparability(before_manifest, after_manifest)

    console = Console(highlight=False)
    console.print(
        f"\n[bold]{before_dir.name[:12]}[/bold] → [bold]{after_dir.name[:12]}[/bold]  "
        f"[dim]threshold {resolved_threshold}[/dim]\n"
    )
    _provenance(console, before_manifest, after_manifest)
    console.print()

    if comparability.suppresses_verdicts:
        _uncomparable(console, comparability, diffs)
    else:
        if comparability.state == UNKNOWN:
            console.print(
                f"[yellow]warning: {comparability.detail}. The verdicts below are "
                f"unverified — pass --judge-version to `drift snapshot` so Drift can "
                f"check them.[/yellow]\n"
            )
        _buckets(console, diffs)

    if removed:
        console.print(
            f"[dim]{len(removed)} case(s) present in {before_dir.name[:12]} and gone from "
            f"{after_dir.name[:12]}: {', '.join(sorted(removed))}[/dim]"
        )


def _buckets(console: Console, diffs: List[CaseDiff]) -> None:
    counts = {bucket: [c for c in diffs if c.bucket == bucket] for bucket in BUCKET_ORDER}
    for bucket in BUCKET_ORDER:
        if counts[bucket]:
            _render(console, bucket, counts[bucket])
    console.print(
        "  ".join(
            f"[{BUCKET_STYLE[b]}]{b} {len(counts[b])}[/{BUCKET_STYLE[b]}]"
            for b in BUCKET_ORDER
        )
    )


def _uncomparable(
    console: Console, comparability: Comparability, diffs: List[CaseDiff]
) -> None:
    """Report the numbers and refuse to draw a conclusion from them.

    Every score-derived bucket is withheld, Unchanged included: two rubrics landing
    on the same score is a coincidence, not a finding, so "unchanged" is as
    unsupportable a claim here as "regressed". New survives, because whether a case
    exists in a snapshot does not depend on who graded it.
    """
    console.print(
        f"[bold red]Not directly comparable — "
        f"{comparability.detail}.[/bold red]"
    )
    console.print(
        "[red]Fixed / Regressed / Improved / Degraded / Unchanged are suppressed: a "
        "verdict on these deltas would be about the rubric, not the model.[/red]\n"
    )
    fresh = [c for c in diffs if c.bucket == "New"]
    judged = [c for c in diffs if c.bucket != "New"]
    if judged:
        _flat(console, judged)
    if fresh:
        _render(console, "New", fresh)
    console.print(
        f"[bold red]Verdicts suppressed {len(judged)}[/bold red]  "
        f"[{BUCKET_STYLE['New']}]New {len(fresh)}[/{BUCKET_STYLE['New']}]"
    )
