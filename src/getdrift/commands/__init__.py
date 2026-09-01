"""Subcommand implementations for the `drift` CLI."""

from pathlib import Path
from typing import Optional

import typer

from getdrift.schema import stale_repo_schemas


def warn_if_schemas_stale(drift: Optional[Path]) -> None:
    """Say so when `.drift/schema/` no longer matches the schemas this build ships."""
    stale = stale_repo_schemas(drift)
    if stale:
        typer.secho(
            f"warning: {', '.join(stale)} in .drift/schema/ differs from the schema "
            f"shipped with this Drift build, and validation reads the repo's copy. "
            "Run `drift init` to refresh it, or upgrade Drift.",
            fg=typer.colors.YELLOW,
            err=True,
        )


def fail(message: object) -> None:
    """Print an error and exit 1. Shared by every subcommand."""
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)
