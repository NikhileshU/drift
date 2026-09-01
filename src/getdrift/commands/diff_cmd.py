"""`drift diff` — bucketed comparison of two snapshots."""

import typer


def diff(
    hash1: str = typer.Argument(..., help="Baseline snapshot commit hash."),
    hash2: str = typer.Argument(..., help="Candidate snapshot commit hash."),
) -> None:
    """Diff two snapshots into Fixed/Regressed/Improved/Degraded/Unchanged/New."""
    raise NotImplementedError("drift diff is implemented in D4a-D4d")
