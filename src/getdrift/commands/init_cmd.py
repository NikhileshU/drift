"""`drift init` — scaffold the .drift/ directory in the repo root."""

import typer


def init(
    force: bool = typer.Option(
        False,
        "--force",
        help="Re-create any missing pieces of an existing .drift/ directory.",
    ),
) -> None:
    """Create the .drift/ directory structure in the current repo."""
    raise NotImplementedError("drift init is implemented in D1c")
