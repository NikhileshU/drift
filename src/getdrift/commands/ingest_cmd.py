"""`drift ingest` — turn another harness's output into a results.json.

One subcommand per harness. The OTel listener is in-process rather than file-based
(`getdrift.adapters.otel.DriftSpanCollector`), so it has no subcommand here.
"""

import json
from pathlib import Path

import typer

from getdrift.adapters.promptfoo import (
    DEFAULT_ENVIRONMENT,
    PromptfooFormatError,
    convert_file,
)
from getdrift.schema import SchemaValidationError

ingest = typer.Typer(
    name="ingest",
    no_args_is_help=True,
    help="Convert an eval harness's output into a Drift results.json.",
)


@ingest.command("promptfoo")
def promptfoo_cmd(
    input_file: Path = typer.Argument(
        ..., exists=True, dir_okay=False, help="JSON written by `promptfoo eval -o`."
    ),
    output: Path = typer.Option(
        Path("results.json"), "--output", "-o", help="Where to write the results.json."
    ),
    environment: str = typer.Option(
        DEFAULT_ENVIRONMENT,
        "--environment",
        help="golden_set (a curated promptfooconfig) or production_sample.",
    ),
) -> None:
    """Convert promptfoo output into a schema-valid results.json."""
    try:
        results = convert_file(input_file, environment=environment)
    except PromptfooFormatError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)
    except SchemaValidationError as exc:
        typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
        for problem in exc.problems:
            typer.secho(f"  - {problem}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    typer.secho(
        f"Wrote {output} — {len(results['cases'])} case(s) from {input_file}.",
        fg=typer.colors.GREEN,
    )
    typer.echo(f"Next: drift snapshot --results-file {output}")
