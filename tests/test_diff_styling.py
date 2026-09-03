"""The two notes that report withheld or lost cases must not wear the dim style.

These assert on rendered ANSI, so they take `forced_color` to normalise the environment
first. Without it they pass on a developer's laptop and fail in CI, where there is no
TTY — which is rich stripping colour correctly, not Drift misbehaving. See the fixture
in conftest for why neither `force_terminal=True` nor `CliRunner(color=True)` is enough.

`dim` is what this tool means by "nothing to see" — it is the Unchanged bucket's own
colour. A note whose whole job is to say something DID happen, painted in the colour
that means it did not, is invisible to anyone scanning the terminal. That regression
survived once because nothing asserted the styling; these tests are why it cannot again.
"""

import io

import pytest
from rich.console import Console

from getdrift.commands.diff_cmd import (
    BUCKET_STYLE,
    REMOVED_MARKER,
    SUPPRESSED_MARKER,
    _environment_mismatch_note,
    _filtered_note,
    _removed_note,
)
from getdrift.diffing import ENVIRONMENT_MISMATCH, CaseDiff

YELLOW, DIM = "\x1b[33m", "\x1b[2m"


def _render(call):
    console = Console(
        file=io.StringIO(), force_terminal=True, color_system="standard", width=200
    )
    call(console)
    return console.file.getvalue()


def _diff(case_id, **flags):
    return CaseDiff(
        case_id=case_id,
        bucket="Unchanged",
        pass_before=True,
        pass_after=True,
        score_before=0.7,
        score_after=0.6,
        delta=-0.1,
        shared_metrics=["accuracy"],
        **flags,
    )


def _env_mismatch_diff(case_id):
    return CaseDiff(
        case_id=case_id,
        bucket=ENVIRONMENT_MISMATCH,
        pass_before=True,
        pass_after=False,
        score_before=1.0,
        score_after=0.2,
        delta=-0.8,
        shared_metrics=["accuracy"],
        environment_before="golden_set",
        environment_after="production_sample",
    )


def test_dim_really_is_the_nothing_happened_colour():
    """The premise the other tests rest on. If this changes, revisit them."""
    assert BUCKET_STYLE["Unchanged"] == "dim"


@pytest.mark.parametrize(
    "flag, fragment",
    [
        ("noise_filtered", "stayed inside the noise floor"),
        ("pass_flip_filtered", "did not survive the majority"),
    ],
)
def test_suppressed_case_notes_are_yellow_not_dim(forced_color, flag, fragment):
    output = _render(lambda c: _filtered_note(c, [_diff("c", **{flag: True})]))
    assert fragment in output
    assert YELLOW in output
    assert DIM not in output


def test_environment_mismatch_note_is_yellow_not_dim(forced_color):
    """P6-A4: a case with no verdict must read the same way as the other two
    withheld-case notes — this one just has no bucket table to hide inside."""
    output = _render(lambda c: _environment_mismatch_note(c, [_env_mismatch_diff("c")]))
    assert "golden_set" in output and "production_sample" in output
    assert YELLOW in output
    assert DIM not in output


def test_removed_cases_note_is_yellow_not_dim(forced_color, git_repo):
    """A removed case appears nowhere else in the output — miss the line, miss it entirely."""
    import subprocess

    from typer.testing import CliRunner

    from getdrift.cli import app
    from tests.test_diffing import DEMO

    runner = CliRunner()
    runner.invoke(app, ["init"])
    runner.invoke(app, ["snapshot", "--results-file", str(DEMO / "baseline.json")])
    first = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, capture_output=True, text=True
    ).stdout.strip()
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "v2"], cwd=git_repo, check=True)
    runner.invoke(app, ["snapshot", "--results-file", str(DEMO / "candidate.json")])
    second = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, capture_output=True, text=True
    ).stdout.strip()

    result = runner.invoke(app, ["diff", first, second], color=True)
    # The note wraps, so the case_id can land on the following line; the style marker
    # is what this test is about and it opens on the first.
    line = next(raw for raw in result.output.splitlines() if "and gone from" in raw)
    assert "legacy_fax_number_lookup" in result.output
    assert YELLOW in line and DIM not in line


# --- the markers, which are what actually survives a CI log -----------------------
#
# Everything above asserts colour, and colour is the bonus. `Console` does ordinary
# TTY detection, so in CI — a pipe, no terminal — rich strips every escape code, which
# is correct behaviour and not something to fight. These tests deliberately do NOT take
# `forced_color`: they run in whatever the ambient environment is and assert on the raw
# characters, because that is the state a CI log is actually in.


def _plain(call):
    """Render with colour unavailable, exactly as an unattended CI run would."""
    console = Console(file=io.StringIO(), force_terminal=False, no_color=True, width=200)
    call(console)
    return console.file.getvalue()


@pytest.mark.parametrize(
    "flag, fragment",
    [
        ("noise_filtered", "stayed inside the noise floor"),
        ("pass_flip_filtered", "did not survive the majority"),
    ],
)
def test_suppressed_notes_carry_a_marker_without_colour(flag, fragment):
    output = _plain(lambda c: _filtered_note(c, [_diff("c", **{flag: True})]))
    assert "\x1b[" not in output, "precondition: this render must have no escape codes"
    # The literal, not the constant: `assert SUPPRESSED_MARKER in output` passes
    # vacuously if the constant is ever emptied, which is exactly the regression this
    # test exists to catch.
    assert "SUPPRESSED:" in output
    assert fragment in output


def test_removed_note_carries_a_marker_without_colour():
    output = _plain(lambda c: _removed_note(c, ["lost-case"], "a" * 40, "b" * 40))
    assert "\x1b[" not in output
    assert "REMOVED:" in output
    assert "lost-case" in output


def test_environment_mismatch_note_carries_a_marker_without_colour():
    output = _plain(lambda c: _environment_mismatch_note(c, [_env_mismatch_diff("c")]))
    assert "\x1b[" not in output
    assert "SUPPRESSED:" in output
    assert "golden_set" in output and "production_sample" in output


def test_the_markers_are_plain_ascii_and_greppable():
    """A unicode sigil would degrade badly in a raw log, and `grep` is the point."""
    assert SUPPRESSED_MARKER == "SUPPRESSED:"
    assert REMOVED_MARKER == "REMOVED:"
    for marker in (SUPPRESSED_MARKER, REMOVED_MARKER):
        assert marker.isascii() and marker.isupper() and marker.endswith(":")


def test_diff_and_ci_report_lost_cases_identically():
    """`_removed_note` had a second copy in ci_cmd. One statement, one implementation.

    Two implementations of the same sentence eventually disagree, and then a diff and a
    ci run tell you different things about the same lost case.
    """
    from getdrift.commands import ci_cmd, diff_cmd

    assert ci_cmd._removed_note is diff_cmd._removed_note
