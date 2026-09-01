"""D5b: the opt-in require_judge_version guard. Off by default, enforced in the library."""
import json

import pytest
from typer.testing import CliRunner

from getdrift.cli import app
from getdrift.snapshot import MissingJudgeVersionError, create_snapshot

runner = CliRunner()


def _init(repo, setting=None):
    runner.invoke(app, ["init"])
    config = repo / ".drift" / "config.yaml"
    if setting is not None:
        config.write_text(config.read_text() + f"\nrequire_judge_version: {setting}\n")
    return repo / ".drift"


def test_off_by_default_the_placeholder_still_snapshots(git_repo, example_results):
    _init(git_repo)
    assert create_snapshot(example_results).manifest["judge_version"] == "unset"


def test_on_the_placeholder_is_refused(git_repo, example_results):
    drift = _init(git_repo, "true")
    with pytest.raises(MissingJudgeVersionError) as exc:
        create_snapshot(example_results)
    assert "require_judge_version" in str(exc.value)
    assert list((drift / "snapshots").iterdir()) == []  # nothing written


def test_on_a_real_judge_version_is_accepted(git_repo, example_results):
    _init(git_repo, "true")
    snap = create_snapshot(example_results, judge_version="rubric@3ab91f")
    assert snap.manifest["judge_version"] == "rubric@3ab91f"
    assert (snap.path / "manifest.json").exists()


def test_the_guard_is_in_the_library_not_the_cli(git_repo, example_results):
    """The whole point: unattended callers (pytest plugin, variance work) are bound too."""
    _init(git_repo, "true")
    with pytest.raises(MissingJudgeVersionError):
        create_snapshot(example_results)  # no Typer anywhere in this call


@pytest.mark.parametrize("setting", ["true", "'true'", '"yes"', "on"])
def test_truthy_spellings_all_enable_it(git_repo, example_results, setting):
    """A safety flag that silently does nothing because it was quoted is worse than none."""
    _init(git_repo, setting)
    with pytest.raises(MissingJudgeVersionError):
        create_snapshot(example_results)


@pytest.mark.parametrize("setting", ["false", "'false'", "no"])
def test_falsy_spellings_leave_it_off(git_repo, example_results, setting):
    _init(git_repo, setting)
    assert create_snapshot(example_results).manifest["judge_version"] == "unset"


def test_cli_reports_it_clearly_and_exits_1(git_repo, example_results):
    _init(git_repo, "true")
    path = git_repo / "r.json"
    path.write_text(json.dumps(example_results))
    result = runner.invoke(app, ["snapshot", "--results-file", str(path)])
    assert result.exit_code == 1
    assert "require_judge_version" in result.output
    assert "cannot be filled in later" in result.output


def test_an_existing_snapshot_still_reports_immutability_first(git_repo, example_results):
    """Ordering: the slot being taken is more informative than the policy."""
    _init(git_repo)
    create_snapshot(example_results)
    config = git_repo / ".drift" / "config.yaml"
    config.write_text(config.read_text() + "\nrequire_judge_version: true\n")
    path = git_repo / "r.json"
    path.write_text(json.dumps(example_results))
    result = runner.invoke(app, ["snapshot", "--results-file", str(path)])
    assert "immutable" in result.output
    assert "require_judge_version" not in result.output


def test_the_template_documents_the_key(git_repo):
    _init(git_repo)
    text = (git_repo / ".drift" / "config.yaml").read_text()
    assert "# require_judge_version: true" in text
