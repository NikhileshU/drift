"""`drift snapshot` — record an immutable eval snapshot for the current commit."""

import json
from datetime import datetime, timezone
from pathlib import Path

import typer

from getdrift import __version__
from getdrift.gitutil import GitError, has_uncommitted_changes, head_hash
from getdrift.paths import drift_dir
from getdrift.schema import (
    SCHEMA_VERSION,
    SchemaValidationError,
    validate_manifest,
    validate_results,
)

PLACEHOLDER = "unset"


def _fail(message: object) -> None:
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _fail_validation(exc: SchemaValidationError, schema_name: str) -> None:
    typer.secho(
        f"error: {exc.source} does not conform to .drift/schema/{schema_name}",
        fg=typer.colors.RED,
        err=True,
    )
    for problem in exc.problems:
        typer.secho(f"  - {problem}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


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
        drift = drift_dir()
        commit = head_hash()
        dirty = has_uncommitted_changes()
    except GitError as exc:
        _fail(exc)

    if not drift.is_dir():
        _fail("no .drift/ directory in this repo — run `drift init` first")

    try:
        results = json.loads(results_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _fail(f"{results_file} does not exist")
    except json.JSONDecodeError as exc:
        _fail(f"{results_file} is not valid JSON: {exc}")

    try:
        validate_results(results, source=str(results_file), drift_dir=drift)
    except SchemaValidationError as exc:
        _fail_validation(exc, "results.schema.json")

    # Immutability: one commit, one snapshot, never rewritten. There is deliberately
    # no --force — overwriting would make every past diff unreproducible.
    target = drift / "snapshots" / commit
    if target.exists():
        _fail(
            f"a snapshot for {commit} already exists at "
            f"{target.relative_to(drift.parent)}\n"
            "       Snapshots are immutable — Drift will not overwrite one. Commit your\n"
            "       changes and snapshot the new commit instead."
        )

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "commit_hash": commit,
        "created_at": _now(),
        "model_version": model_version,
        "prompt_version": prompt_version,
        "judge_version": judge_version,
        "drift_version": __version__,
        "case_count": len(results["cases"]),
    }
    # Validated before anything is written, so a bad manifest cannot leave a
    # half-built snapshot directory that then blocks the retry.
    try:
        validate_manifest(manifest, source="generated manifest.json", drift_dir=drift)
    except SchemaValidationError as exc:
        _fail_validation(exc, "manifest.schema.json")

    target.mkdir(parents=True)
    for name, document in (("results.json", results), ("manifest.json", manifest)):
        (target / name).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")

    if dirty:
        typer.secho(
            "warning: the working tree has uncommitted changes, so this snapshot is "
            f"labelled with a commit ({commit[:8]}) that does not describe the code "
            "that produced it.",
            fg=typer.colors.YELLOW,
            err=True,
        )
    typer.secho(f"Snapshot written: {target.relative_to(drift.parent)}", fg=typer.colors.GREEN)
    typer.echo(f"  commit        {commit}")
    typer.echo(f"  cases         {manifest['case_count']}")
    typer.echo(f"  judge_version {judge_version}")
    if judge_version == PLACEHOLDER:
        typer.secho(
            "  (judge_version is a placeholder — pass --judge-version so `drift diff` "
            "can tell whether two snapshots are comparable)",
            fg=typer.colors.YELLOW,
        )
