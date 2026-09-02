"""The two notes that report withheld or lost cases must not wear the dim style.

`dim` is what this tool means by "nothing to see" — it is the Unchanged bucket's own
colour. A note whose whole job is to say something DID happen, painted in the colour
that means it did not, is invisible to anyone scanning the terminal. That regression
survived once because nothing asserted the styling; these tests are why it cannot again.
"""

import io

import pytest
from rich.console import Console

from getdrift.commands.diff_cmd import BUCKET_STYLE, _filtered_note
from getdrift.diffing import CaseDiff

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
def test_suppressed_case_notes_are_yellow_not_dim(flag, fragment):
    output = _render(lambda c: _filtered_note(c, [_diff("c", **{flag: True})]))
    assert fragment in output
    assert YELLOW in output
    assert DIM not in output


def test_removed_cases_note_is_yellow_not_dim(git_repo):
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
