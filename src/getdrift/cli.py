"""Typer entry point for the `drift` command."""

import typer

from getdrift import __version__

app = typer.Typer(
    name="drift",
    add_completion=False,
    no_args_is_help=True,
    help="Drift — immutable eval snapshots per git commit, and diffs between them.",
)


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", help="Show the Drift version and exit."
    ),
) -> None:
    if version:
        typer.echo(f"drift {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


def main() -> None:
    """Console-script entry point registered as `drift`."""
    app()


if __name__ == "__main__":
    main()
