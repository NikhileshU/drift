"""`drift diff` — bucketed comparison of two snapshots."""

from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

from getdrift.commands import fail, warn_if_schemas_stale
from getdrift.diffing import (
    BUCKET_ORDER,
    DEFAULT_NOISE_SIGMA,
    DEFAULT_THRESHOLD,
    ENVIRONMENT_MISMATCH,
    UNKNOWN,
    CaseDiff,
    Comparability,
    DuplicateCaseIdError,
    Environment,
    compare,
    filter_environment,
    judge_comparability,
)
from getdrift.gitutil import GitError
from getdrift.paths import drift_dir, read_config
from getdrift.snapshot import Snapshot, SnapshotError, load_snapshot
from getdrift.trend import load_history

BUCKET_STYLE = {
    "Regressed": "bold red",
    "Degraded": "yellow",
    "Fixed": "bold green",
    "Improved": "green",
    "New": "cyan",
    "Unchanged": "dim",
}

#: Provenance shown in the diff header. Only `judge_version` gates anything; the other
#: two are there because "what else moved?" is the first question a human asks when a
#: diff comes back flagged as uncomparable.
PROVENANCE = ("judge_version", "model_version", "prompt_version")

#: Plain-ASCII prefixes on the two notes that report withheld or lost cases. They exist
#: because colour does not survive a CI log, and these are the lines that must. Distinct
#: from the lowercase `warning:` used elsewhere: nothing here is wrong, something is
#: being withheld or has gone missing, and the two deserve different words.
SUPPRESSED_MARKER = "SUPPRESSED:"
REMOVED_MARKER = "REMOVED:"


def _threshold(drift: Path, override: Optional[float]) -> float:
    if override is not None:
        return override
    value = read_config(drift).get("diff_threshold")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return DEFAULT_THRESHOLD


def _noise_sigma(drift: Path, override: Optional[float]) -> float:
    if override is not None:
        return override
    value = read_config(drift).get("noise_sigma")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return DEFAULT_NOISE_SIGMA


def _filtered_note(console: Console, diffs: List[CaseDiff]) -> None:
    """Say what the noise filter withheld, and why. Suppressed is not hidden.

    Two separate reasons, reported separately: a score that moved past the raw
    threshold but stayed inside the noise floor, and a pass flip that did not survive
    the majority across runs. Both cases still appear in Unchanged with their real
    numbers; this is the line that stops them looking like nothing happened.

    Painted yellow for that reason. `dim` is what this tool means by "nothing to see" —
    it is the Unchanged bucket's own colour — so a note whose entire job is to say
    something DID happen must not be wearing it.

    But colour cannot be the only carrier. `Console` does ordinary TTY auto-detection,
    and CI captures output through a pipe, so rich correctly strips every escape code
    the moment this runs unattended — which is precisely where a Drift diff is read
    with nobody looking at a coloured terminal. Without a marker the note then renders
    byte-for-byte like any other line in the log. Hence the literal `SUPPRESSED:`
    prefix: plain ASCII, survives colour stripping, and greppable, which colour never
    was. Colour is the bonus for interactive use, not the signal.

    The harm in one line: `grep -i warning` over a CI log finds the judge-version
    warning and finds nothing at all for a suppressed case. That is why the fix is a
    marker and not a better colour.
    """
    noisy = [c for c in diffs if c.noise_filtered]
    flips = [c for c in diffs if c.pass_flip_filtered]
    for cases, reason in (
        (noisy, "moved past the threshold but stayed inside the noise floor"),
        (flips, "had a pass flip that did not survive the majority across runs"),
    ):
        if cases:
            console.print(
                f"[yellow]{SUPPRESSED_MARKER} {len(cases)} case(s) {reason}: "
                f"{', '.join(sorted(c.case_id for c in cases))}[/yellow]"
            )


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


def _provenance(console: Console, before: Snapshot, after: Snapshot) -> None:
    for field in PROVENANCE:
        old = (before.manifest or {}).get(field) or "—"
        new = (after.manifest or {}).get(field) or "—"
        line = f"{field:<15}{old} → {new}"
        console.print(f"[yellow]{line}[/yellow]" if old != new else f"[dim]{line}[/dim]")


def _flat(console: Console, cases: List[CaseDiff]) -> None:
    """Every case's raw numbers with no bucket column — facts without a verdict."""
    table = Table(
        title=f"Scores only, no verdict ({len(cases)})",
        title_style="bold yellow",
        title_justify="left",
        header_style="bold",
        border_style="yellow",
    )
    for column in ("case_id", "pass", "before", "after", "delta"):
        table.add_column(column, overflow="fold", justify="right" if column != "case_id" else "left")
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

    Every score-derived bucket is withheld, Unchanged included: two rubrics landing on
    the same score is a coincidence, not a finding, so "unchanged" is as unsupportable a
    claim here as "regressed". New survives, because whether a case exists in a snapshot
    does not depend on who graded it.
    """
    console.print(f"[bold red]Not directly comparable — {comparability.detail}.[/bold red]")
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


def _environment_mismatch_note(console: Console, diffs: List[CaseDiff]) -> None:
    """Say which cases were compared across two different `environment`s, and why no
    verdict is shown for them.

    Each such case is still in `diffs` with `bucket == ENVIRONMENT_MISMATCH`;
    `_buckets` already skips it, since that value is not one of the six in
    BUCKET_ORDER — without this note it would just vanish from the report, the same
    way a removed case would without `_removed_note`. Reuses `_flat` for the raw
    numbers, exactly as `_uncomparable` does for a judge-version mismatch, and the
    same `SUPPRESSED:` marker convention as `_filtered_note`: plain ASCII, survives
    colour stripping in CI, greppable.
    """
    mismatched = [c for c in diffs if c.bucket == ENVIRONMENT_MISMATCH]
    if not mismatched:
        return
    _flat(console, mismatched)
    names = ", ".join(
        f"{c.case_id} ({c.environment_before} vs {c.environment_after})"
        for c in sorted(mismatched, key=lambda c: c.case_id)
    )
    console.print(
        f"[yellow]{SUPPRESSED_MARKER} {len(mismatched)} case(s) compared across "
        f"different environments, no verdict shown: {names}. Pass --environment "
        "<golden_set|production_sample> to compare only one.[/yellow]"
    )


def _most_recent(drift: Path, count: int) -> List[Snapshot]:
    """The `count` newest snapshots, oldest first, or fail with a clear reason.

    Built on `load_history()` from `trend.py` rather than a second ordering: it
    already sorts by the manifest's `created_at` (falling back to commit ancestry for
    same-second ties) and already tolerates undated or unreadable snapshots.
    """
    history = load_history(drift)
    if len(history) < count:
        noun = "snapshot" if len(history) == 1 else "snapshots"
        fail(
            f"only {len(history)} {noun} in .drift/snapshots/ — need at least {count}. "
            "`drift snapshot` a few more commits, or pass both hashes explicitly."
        )
    return history[-count:]


def diff(
    hash1: Optional[str] = typer.Argument(
        None,
        help="Baseline snapshot commit hash (or a prefix). Omit both arguments to "
        "compare the two most recent snapshots; give only this one to compare it "
        "against the most recent — the same direction the two-hash form already "
        "reads in (baseline -> candidate, older -> newer).",
    ),
    hash2: Optional[str] = typer.Argument(
        None, help="Candidate snapshot commit hash (or a prefix)."
    ),
    threshold: Optional[float] = typer.Option(
        None,
        "--threshold",
        help=f"Score delta that counts as Improved/Degraded. Defaults to "
        f"`diff_threshold` in .drift/config.yaml, else {DEFAULT_THRESHOLD}.",
    ),
    noise_sigma: Optional[float] = typer.Option(
        None,
        "--noise-sigma",
        help=f"How many combined standard deviations a change must clear before it is "
        f"called Improved/Degraded. Defaults to `noise_sigma` in .drift/config.yaml, "
        f"else {DEFAULT_NOISE_SIGMA}. Pass 0 to disable the noise filter and bucket on "
        f"the raw threshold alone.",
    ),
    environment: Optional[Environment] = typer.Option(
        None,
        "--environment",
        help="Compare only cases from this environment, applied to both snapshots "
        "before matching by case_id. Without it, a case_id scored under one "
        "environment in one snapshot and the other environment in the other still "
        "gets matched, but its verdict is suppressed rather than shown as a false "
        "regression or improvement.",
    ),
) -> None:
    """Diff two snapshots into Fixed/Regressed/Improved/Degraded/Unchanged/New.

    With no arguments, compares the two most recent snapshots by timestamp — "what did
    my last change do" is the question this command exists to answer, and it should
    cost no typing. With one, compares that snapshot against the most recent.
    """
    try:
        drift = drift_dir()
    except GitError as exc:
        fail(exc)
    warn_if_schemas_stale(drift)
    try:
        if hash1 is None:
            before, after = _most_recent(drift, 2)
        elif hash2 is None:
            before = load_snapshot(hash1, drift)
            after = _most_recent(drift, 1)[0]
        else:
            before, after = load_snapshot(hash1, drift), load_snapshot(hash2, drift)
    except SnapshotError as exc:
        fail(exc)
    if before.path == after.path:
        fail(f"both arguments resolve to the same snapshot ({before.path.name})")

    resolved_threshold = _threshold(drift, threshold)
    resolved_sigma = _noise_sigma(drift, noise_sigma)
    resolved_env = environment.value if environment is not None else None
    try:
        diffs, removed = compare(
            filter_environment(before.results, resolved_env),
            filter_environment(after.results, resolved_env),
            resolved_threshold,
            resolved_sigma,
        )
    except DuplicateCaseIdError as exc:
        fail(exc)

    comparability = judge_comparability(before.manifest, after.manifest)

    console = Console(highlight=False)
    console.print(
        f"\n[bold]{before.path.name[:12]}[/bold] → [bold]{after.path.name[:12]}[/bold]  "
        f"[dim]threshold {resolved_threshold}  noise {resolved_sigma}\u03c3[/dim]\n"
    )
    _provenance(console, before, after)
    console.print()

    if comparability.suppresses_verdicts:
        _uncomparable(console, comparability, diffs)
    else:
        if comparability.state == UNKNOWN:
            console.print(
                f"[yellow]warning: {comparability.detail}. The verdicts below are "
                "unverified — pass --judge-version to `drift snapshot` so Drift can "
                "check them.[/yellow]\n"
            )
        _buckets(console, diffs)
        _filtered_note(console, diffs)
        _environment_mismatch_note(console, diffs)

    if removed:
        # Yellow, on a narrower argument than "removals matter". A suppressed case is
        _removed_note(console, removed, before.path.name, after.path.name)


def _removed_note(console: Console, removed: List[str], before: str, after: str) -> None:
    """The cases that were in the baseline and are gone from the candidate.

    Lives here, and is imported by `drift ci`, rather than existing twice. `drift diff`
    and `drift ci` making the same statement in two places is how they end up making it
    differently — the same reason `case_stats` has one home.

    Marked and coloured for the reason above `_filtered_note`, with one addition: a
    suppressed case is still visible in the Unchanged table with its real numbers, so
    its note is supplementary. A removed case appears NOWHERE else in the output. Miss
    this line and there is no other signal that the eval set shrank, and it is also how
    a changed `case_id` manifests — silent loss of coverage rather than a deliberate
    edit. Information that appears exactly once must survive a monochrome log.
    """
    if removed:
        console.print(
            f"[yellow]{REMOVED_MARKER} {len(removed)} case(s) present in {before[:12]} "
            f"and gone from {after[:12]}: {', '.join(sorted(removed))}[/yellow]"
        )
