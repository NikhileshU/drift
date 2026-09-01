"""`drift ingest` — turn another harness's output into a results.json.

One subcommand per harness. The OTel listener is in-process rather than file-based
(`getdrift.adapters.otel.DriftSpanCollector`), so it has no subcommand here.
"""

import json
import shlex
from pathlib import Path

import typer

from getdrift.adapters.promptfoo import (
    DEFAULT_ENVIRONMENT,
    PromptfooFormatError,
    convert_file,
)
from getdrift.gitutil import GitError
from getdrift.paths import drift_dir
from getdrift.schema import SchemaValidationError


def _repo_schema_dir():
    """The repo's own .drift/, so `ingest` validates against what `snapshot` will use.

    None outside a repo or before `drift init` — ingest is useful in both, and the
    packaged schema is the right fallback there."""
    try:
        base = drift_dir()
    except GitError:
        return None
    return base if base.is_dir() else None

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
        results = convert_file(
            input_file, environment=environment, drift_dir=_repo_schema_dir()
        )
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

    # promptfoo knows its provider, its prompts and its assertions, so the snapshot's
    # provenance fields can all be filled in. Left at the `unset` placeholder,
    # judge_version would make `drift diff`'s comparability check meaningless.
    fields = results["metadata"]["provenance"]
    typer.echo("Next:")
    typer.echo(f"  drift snapshot --results-file {output} \\")
    for flag in ("model-version", "prompt-version", "judge-version"):
        value = fields[flag.replace("-", "_")]
        end = "" if flag == "judge-version" else " \\"
        typer.echo(f"    --{flag} {shlex.quote(value)}{end}")
