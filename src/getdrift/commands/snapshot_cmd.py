"""`drift snapshot` — record an immutable eval snapshot for the current commit."""

from pathlib import Path
from typing import Optional

import typer


def snapshot(
    results_file: Optional[Path] = typer.Option(
        None,
        "--results-file",
        help="Path to a results.json conforming to .drift/schema/results.schema.json.",
    ),
) -> None:
    """Snapshot eval results against the current git commit hash."""
    raise NotImplementedError("drift snapshot is implemented in D3a-D3d")
