"""Subcommand implementations for the `drift` CLI."""

import typer


def fail(message: object) -> None:
    """Print an error and exit 1. Shared by every subcommand."""
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)
