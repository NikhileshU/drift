"""`drift init` — scaffold the .drift/ directory in the repo root."""

from pathlib import Path

import typer

from getdrift.gitutil import GitError
from getdrift.paths import drift_dir
from getdrift.schema import SCHEMAS_DIR

CONFIG_TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "config.yaml"


def _report(path: Path, root: Path, created: bool) -> None:
    colour = typer.colors.GREEN if created else typer.colors.YELLOW
    label = "created" if created else "exists "
    typer.secho(f"  {label}  {path.relative_to(root)}", fg=colour)


def init(
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite an existing .drift/config.yaml with the stub template.",
    ),
) -> None:
    """Create the .drift/ directory structure in the current repo."""
    try:
        drift = drift_dir()
    except GitError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    root = drift.parent
    already = drift.is_dir()
    typer.echo(f"Initializing Drift in {root}")

    for directory in (drift, drift / "schema", drift / "golden_set", drift / "snapshots"):
        created = not directory.is_dir()
        directory.mkdir(parents=True, exist_ok=True)
        _report(directory, root, created)

    # The schemas are the contract: always refresh them to match the installed Drift
    # version, since a stale on-disk schema would silently change what validates.
    for source in sorted(SCHEMAS_DIR.glob("*.schema.json")):
        target = drift / "schema" / source.name
        existed = target.exists()
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        _report(target, root, created=not existed)

    config = drift / "config.yaml"
    if config.exists() and not force:
        _report(config, root, created=False)
    else:
        overwritten = config.exists()
        config.write_text(CONFIG_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
        _report(config, root, created=not overwritten)
        if overwritten:
            typer.secho("  (config.yaml reset by --force)", fg=typer.colors.YELLOW)

    if already:
        typer.secho(
            "\n.drift/ already existed — missing pieces were filled in.",
            fg=typer.colors.YELLOW,
        )
    else:
        typer.secho("\nDrift initialized.", fg=typer.colors.GREEN, bold=True)
    typer.echo("Next: drift snapshot --results-file <path/to/results.json>")
