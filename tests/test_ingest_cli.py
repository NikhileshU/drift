"""P8-J1 follow-up: `drift ingest promptfoo` — the CLI command body itself.

`convert_file` (the underlying adapter) is well covered in test_promptfoo_adapter.py.
This file is about the wrapper: does a user who feeds it bad input get a readable
message and the right exit code, not a traceback or a silent wrong answer.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import getdrift.commands.ingest_cmd as ingest_cmd
from getdrift.cli import app
from getdrift.adapters.promptfoo import PromptfooFormatError
from getdrift.schema import SchemaValidationError

runner = CliRunner()
EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "promptfoo"
REAL_OUTPUT = EXAMPLES / "out.json"


def test_a_valid_file_writes_results_and_exits_zero(tmp_path, git_repo):
    output = tmp_path / "results.json"
    result = runner.invoke(app, ["ingest", "promptfoo", str(REAL_OUTPUT), "-o", str(output)])
    assert result.exit_code == 0, result.output
    assert "Wrote" in result.output and "case(s)" in result.output
    written = json.loads(output.read_text())
    assert len(written["cases"]) == 2  # matches test_promptfoo_adapter.py's real fixture


def test_malformed_json_shape_exits_one_with_a_readable_message(tmp_path):
    bad = tmp_path / "not-promptfoo.json"
    bad.write_text(json.dumps({"nothing": "to see here"}))
    result = runner.invoke(app, ["ingest", "promptfoo", str(bad)])
    assert result.exit_code == 1
    assert "error:" in result.output
    assert "Traceback" not in result.output


def test_schema_validation_failure_exits_one_and_lists_the_problems(tmp_path, monkeypatch):
    """Exercises the command's own SchemaValidationError branch directly — see module
    docstring: convert_file's real validation is the adapter's job and is tested there;
    this file tests what the CLI does with that failure, not how to provoke one."""
    def _raise(*args, **kwargs):
        raise SchemaValidationError("results.json", ["cases: too short", "cases[0].pass: required"])
    monkeypatch.setattr(ingest_cmd, "convert_file", _raise)

    result = runner.invoke(app, ["ingest", "promptfoo", str(REAL_OUTPUT)])
    assert result.exit_code == 1
    assert "error:" in result.output
    assert "cases: too short" in result.output
    assert "cases[0].pass: required" in result.output


def test_promptfoo_format_error_exits_one_with_its_own_message(tmp_path, monkeypatch):
    def _raise(*args, **kwargs):
        raise PromptfooFormatError("the promptfoo output contains no result rows.")
    monkeypatch.setattr(ingest_cmd, "convert_file", _raise)

    result = runner.invoke(app, ["ingest", "promptfoo", str(REAL_OUTPUT)])
    assert result.exit_code == 1
    assert "no result rows" in result.output
    assert "Traceback" not in result.output


def test_input_file_that_does_not_exist_gives_a_readable_message_not_a_traceback(tmp_path):
    """The most common real user error with `ingest <path>` — a typo'd path. Click's
    own `exists=True` on the argument should catch this before the command body runs."""
    missing = tmp_path / "does-not-exist.json"
    result = runner.invoke(app, ["ingest", "promptfoo", str(missing)])
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "does not exist" in result.output
