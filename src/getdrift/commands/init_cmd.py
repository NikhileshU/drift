"""`drift init` — scaffold the .drift/ directory in the repo root."""

from pathlib import Path

import typer

from getdrift.gitutil import GitError
from getdrift.paths import DriftPaths
from getdrift.resources import config_template, packaged_schemas

def _report(path: Path, root: Path, created: bool) -> None:
    rel = path.relative_to(root)
    if created:
        typer.secho(f"  created  {rel}", fg=typer.colors.GREEN)
    else:
        typer.secho(f"  exists   {rel}", fg=typer.colors.YELLOW)


def init(
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite an existing .drift/config.yaml with the stub template.",
    ),
) -> None:
    """Create the .drift/ directory structure in the current repo."""
    try:
        paths = DriftPaths.discover()
    except GitError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    root = paths.repo_root
    already = paths.initialized
    typer.echo(f"Initializing Drift in {root}")

    for directory in (
        paths.drift_dir,
        paths.schema_dir,
        paths.golden_set_dir,
        paths.snapshots_dir,
    ):
        created = not directory.is_dir()
        directory.mkdir(parents=True, exist_ok=True)
        _report(directory, root, created)

    # The schemas are the contract: always refresh them to match the installed
    # Drift version, since a stale on-disk schema would silently change validation.
    for filename, contents in packaged_schemas().items():
        target = paths.schema_dir / filename
        existed = target.exists()
        changed = not existed or target.read_text(encoding="utf-8") != contents
        if changed:
            target.write_text(contents, encoding="utf-8")
        _report(target, root, created=not existed)
        if existed and changed:
            typer.secho(
                f"  (updated {target.name} to the schema shipped with this Drift build)",
                fg=typer.colors.YELLOW,
            )

    if paths.config_file.exists() and not force:
        _report(paths.config_file, root, created=False)
    else:
        overwritten = paths.config_file.exists()
        paths.config_file.write_text(config_template(), encoding="utf-8")
        _report(paths.config_file, root, created=not overwritten)
        if overwritten:
            typer.secho(
                "  (config.yaml reset to the stub template by --force)",
                fg=typer.colors.YELLOW,
            )

    if already:
        typer.secho(
            "\n.drift/ already existed — missing pieces were filled in, "
            "nothing else was touched.",
            fg=typer.colors.YELLOW,
        )
    else:
        typer.secho("\nDrift initialized.", fg=typer.colors.GREEN, bold=True)
    typer.echo("Next: drift snapshot --results-file <path/to/results.json>")
