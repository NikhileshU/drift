"""`drift ci` — the same comparison as `drift diff`, with an exit code CI can gate on.

Rendering is imported from `diff_cmd` rather than reimplemented: a CI log that showed a
differently-shaped table from the one people read locally would be its own bug, and the
spec asks for the same table.
"""

from enum import Enum
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console

from getdrift.commands import fail, warn_if_schemas_stale
from getdrift.commands.diff_cmd import (
    _buckets,
    _filtered_note,
    _removed_note,
    _noise_sigma,
    _provenance,
    _threshold,
    _uncomparable,
)
from getdrift.diffing import UNKNOWN, DuplicateCaseIdError, compare, judge_comparability
from getdrift.gitutil import GitError, commits_on, has_uncommitted_changes, head_hash
from getdrift.paths import drift_dir, read_config
from getdrift.snapshot import SnapshotError, load_snapshot

#: Any non-zero exit fails the build. One code, deliberately: a judge-version change
#: blocks CI exactly as a real regression does, and splitting them would collide with
#: the code Click already uses for usage errors.
EXIT_OK, EXIT_FAIL = 0, 1

DEFAULT_BRANCH = "main"


class FailOn(str, Enum):
    """Which buckets fail the build."""

    regression = "regression"
    degraded = "degraded"


#: Degraded is a *score* drop with both runs still passing; regression is a pass->fail.
#: The stricter mode is a superset, never a different set.
BLOCKING = {
    FailOn.regression: ("Regressed",),
    FailOn.degraded: ("Regressed", "Degraded"),
}


def _default_baseline(drift: Path, branch: str, exclude: Optional[str]) -> str:
    """Newest commit on `branch` that has a snapshot and is not the one under test.

    `exclude` matters on a push build, where HEAD is itself on the default branch: the
    newest snapshot there would be the current commit, and the gate would compare a
    snapshot against itself. A baseline is by definition something other than the thing
    being tested, so skip it and take the one before.
    """
    try:
        history = commits_on(branch)
    except GitError as exc:
        fail(
            f"could not read branch {branch!r} to pick a baseline ({exc}). Pass "
            "--baseline explicitly, or set `default_branch` in .drift/config.yaml."
        )
    snapshots = drift / "snapshots"
    for commit in history:
        if commit != exclude and (snapshots / commit).is_dir():
            return commit
    fail(
        f"no snapshot found on {branch!r}. Pass --baseline explicitly, or run "
        "`drift snapshot` on that branch first."
    )


def ci(
    baseline: Optional[str] = typer.Option(
        None,
        "--baseline",
        help="Snapshot to compare against. Defaults to the newest commit on the "
        "`default_branch` from .drift/config.yaml that has a snapshot.",
    ),
    current: Optional[str] = typer.Option(
        None, "--current", help="Snapshot under test. Defaults to HEAD."
    ),
    fail_on: FailOn = typer.Option(
        FailOn.regression,
        "--fail-on",
        help="`regression` fails on a pass->fail case. `degraded` also fails on a "
        "score drop that clears the threshold with both runs still passing.",
    ),
    threshold: Optional[float] = typer.Option(
        None, "--threshold", help="Score delta counted as Improved/Degraded."
    ),
    noise_sigma: Optional[float] = typer.Option(
        None, "--noise-sigma", help="Combined stddevs a change must clear to count."
    ),
) -> None:
    """Compare two snapshots and exit non-zero if the gate fails. For CI."""
    try:
        drift = drift_dir()
    except GitError as exc:
        fail(exc)
    warn_if_schemas_stale(drift)

    dirty = False
    if current is None:
        try:
            current, dirty = head_hash(), has_uncommitted_changes()
        except GitError as exc:
            fail(exc)

    # Resolved after `current`, so the default baseline can exclude it.
    if baseline is None:
        branch = read_config(drift).get("default_branch") or DEFAULT_BRANCH
        baseline = _default_baseline(drift, str(branch), current)

    try:
        before, after = load_snapshot(baseline, drift), load_snapshot(current, drift)
    except SnapshotError as exc:
        fail(exc)
    if before.path == after.path:
        fail(
            f"baseline and current resolve to the same snapshot ({before.path.name[:12]}); "
            "there is nothing to gate on."
        )

    resolved_threshold = _threshold(drift, threshold)
    resolved_sigma = _noise_sigma(drift, noise_sigma)
    try:
        diffs, removed = compare(
            before.results, after.results, resolved_threshold, resolved_sigma
        )
    except DuplicateCaseIdError as exc:
        fail(exc)
    comparability = judge_comparability(before.manifest, after.manifest)

    console = Console(highlight=False)
    console.print(
        f"\n[bold]{before.path.name[:12]}[/bold] → [bold]{after.path.name[:12]}[/bold]  "
        f"[dim]threshold {resolved_threshold}  noise {resolved_sigma}σ  "
        f"fail-on {fail_on.value}[/dim]\n"
    )
    if dirty:
        console.print(
            "[yellow]warning: the working tree has uncommitted changes, so the "
            f"snapshot for {current[:8]} may not describe the code under test.[/yellow]\n"
        )
    _provenance(console, before, after)
    console.print()

    # A judge change is checked before the buckets because it decides whether the
    # buckets mean anything at all.
    if comparability.suppresses_verdicts:
        _uncomparable(console, comparability, diffs)
        _removed_note(console, removed, before.path.name, after.path.name)
        console.print(
            f"\n[bold red]FAIL — {comparability.detail}.[/bold red]\n"
            "[red]The gate cannot pass: with the rubric changed, a clean diff is not "
            "evidence that nothing broke. Re-snapshot the baseline under the new judge "
            "version, then re-run.[/red]"
        )
        raise typer.Exit(code=EXIT_FAIL)

    if comparability.state == UNKNOWN:
        console.print(
            f"[yellow]warning: {comparability.detail}. The gate below is unverified — "
            "pass --judge-version to `drift snapshot`, or set `require_judge_version` "
            "in .drift/config.yaml to make it mandatory.[/yellow]\n"
        )
    _buckets(console, diffs)
    _filtered_note(console, diffs)
    _removed_note(console, removed, before.path.name, after.path.name)

    blocking = BLOCKING[fail_on]
    offenders = [case for case in diffs if case.bucket in blocking]
    if offenders:
        names = ", ".join(sorted(case.case_id for case in offenders))
        console.print(
            f"\n[bold red]FAIL — {len(offenders)} case(s) in "
            f"{'/'.join(blocking)}: {names}[/bold red]"
        )
        raise typer.Exit(code=EXIT_FAIL)

    console.print(
        f"\n[bold green]PASS — nothing in {'/'.join(blocking)}.[/bold green]"
    )

