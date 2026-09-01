"""`drift snapshot` — thin CLI wrapper over getdrift.snapshot.create_snapshot."""

from pathlib import Path

import typer

from getdrift.commands import fail, warn_if_schemas_stale
from getdrift.gitutil import GitError
from getdrift.paths import drift_dir
from getdrift.schema import SchemaValidationError
from getdrift.snapshot import PLACEHOLDER, SnapshotError, SnapshotExistsError, create_snapshot


def snapshot(
    results_file: Path = typer.Option(
        ...,
        "--results-file",
        help="Path to a results.json conforming to .drift/schema/results.schema.json.",
    ),
    model_version: str = typer.Option(
        PLACEHOLDER, "--model-version", help="Model under test, free text."
    ),
    prompt_version: str = typer.Option(
        PLACEHOLDER, "--prompt-version", help="Prompt / agent config version, free text."
    ),
    judge_version: str = typer.Option(
        PLACEHOLDER,
        "--judge-version",
        help="Scoring rubric / judge version. `drift diff` compares this between "
        "snapshots to decide whether their scores are comparable at all.",
    ),
) -> None:
    """Snapshot eval results against the current git commit hash."""
    try:
        warn_if_schemas_stale(drift_dir())
    except GitError:
        pass  # create_snapshot reports the git problem properly a moment later
    try:
        snap = create_snapshot(
            results_file,
            model_version=model_version,
            prompt_version=prompt_version,
            judge_version=judge_version,
        )
    except SnapshotExistsError as exc:
        fail(
            f"a snapshot for {exc.commit_hash} already exists at {exc.shown}\n"
            "       Snapshots are immutable — Drift will not overwrite one. Commit your\n"
            "       changes and snapshot the new commit instead."
        )
    except (GitError, SnapshotError) as exc:
        fail(exc)
    except SchemaValidationError as exc:
        typer.secho(
            f"error: {exc.source} does not conform to .drift/schema/{exc.schema}",
            fg=typer.colors.RED,
            err=True,
        )
        for problem in exc.problems:
            typer.secho(f"  - {problem}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    for warning in snap.warnings:
        typer.secho(f"warning: {warning}", fg=typer.colors.YELLOW, err=True)
    if snap.dirty:
        typer.secho(
            "warning: the working tree has uncommitted changes, so this snapshot is "
            f"labelled with a commit ({snap.commit_hash[:8]}) that does not describe "
            "the code that produced it.",
            fg=typer.colors.YELLOW,
            err=True,
        )
    typer.secho(
        f"Snapshot written: {snap.path.relative_to(snap.path.parent.parent.parent)}",
        fg=typer.colors.GREEN,
    )
    typer.echo(f"  commit        {snap.commit_hash}")
    typer.echo(f"  cases         {snap.manifest['case_count']}")
    typer.echo(f"  judge_version {judge_version}")
    if judge_version == PLACEHOLDER:
        typer.secho(
            "  (judge_version is a placeholder — pass --judge-version so `drift diff` "
            "can tell whether two snapshots are comparable)",
            fg=typer.colors.YELLOW,
        )
