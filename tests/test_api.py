"""The importable API other packages call — no Typer, no subprocess."""
import json
import subprocess

import pytest

from getdrift.gitutil import GitError
from getdrift.schema import SchemaValidationError
from getdrift.snapshot import (
    NotInitializedError,
    ResultsFileError,
    SnapshotExistsError,
    SnapshotNotFoundError,
    create_snapshot,
    load_snapshot,
    resolve_snapshot,
)
from tests.test_diffing import DEMO
from typer.testing import CliRunner

from getdrift.cli import app

runner = CliRunner()


def test_create_snapshot_from_a_dict_needs_no_temp_file(git_repo, example_results):
    runner.invoke(app, ["init"])
    snap = create_snapshot(example_results, judge_version="rubric@1")
    assert snap.path.is_dir()
    assert snap.manifest["judge_version"] == "rubric@1"
    assert snap.results == example_results
    assert json.loads((snap.path / "manifest.json").read_text()) == snap.manifest


def test_create_snapshot_from_a_path(git_repo, example_results):
    runner.invoke(app, ["init"])
    path = git_repo / "r.json"
    path.write_text(json.dumps(example_results))
    assert create_snapshot(path).commit_hash == snap_head(git_repo)


def snap_head(repo):
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()


def test_immutability_raises_a_typed_error_a_caller_can_catch(git_repo, example_results):
    runner.invoke(app, ["init"])
    first = create_snapshot(example_results)
    with pytest.raises(SnapshotExistsError) as exc:
        create_snapshot(example_results)
    assert exc.value.commit_hash == first.commit_hash
    assert exc.value.path == first.path


def test_uninitialized_repo_raises_not_initialized(git_repo, example_results):
    with pytest.raises(NotInitializedError):
        create_snapshot(example_results)


def test_invalid_results_raise_schema_validation_error(git_repo, invalid_results):
    runner.invoke(app, ["init"])
    with pytest.raises(SchemaValidationError) as exc:
        create_snapshot(invalid_results)
    assert exc.value.schema == "results.schema.json"
    assert any("duplicate case_id" in p for p in exc.value.problems)


def test_missing_results_file_raises_results_file_error(git_repo):
    runner.invoke(app, ["init"])
    with pytest.raises(ResultsFileError):
        create_snapshot(git_repo / "nope.json")


def test_outside_a_git_repo_raises_git_error(tmp_path, monkeypatch, example_results):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(GitError):
        create_snapshot(example_results)


def test_load_snapshot_returns_results_and_manifest(git_repo):
    runner.invoke(app, ["init"])
    created = create_snapshot(DEMO / "baseline.json", judge_version="rubric@1")
    loaded = load_snapshot(created.commit_hash[:8])
    assert loaded.commit_hash == created.commit_hash
    assert loaded.results == created.results
    assert loaded.manifest["judge_version"] == "rubric@1"


def test_unknown_and_ambiguous_refs_raise_not_found(git_repo):
    runner.invoke(app, ["init"])
    create_snapshot(DEMO / "baseline.json")
    with pytest.raises(SnapshotNotFoundError):
        resolve_snapshot("deadbeef")


def test_load_tolerates_a_missing_manifest(git_repo):
    runner.invoke(app, ["init"])
    created = create_snapshot(DEMO / "baseline.json")
    (created.path / "manifest.json").unlink()
    assert load_snapshot(created.commit_hash).manifest is None


def test_unserialisable_metadata_is_refused_without_poisoning_the_commit(
    git_repo, example_results
):
    """`metadata` is free-form, so an in-process caller can hand us any object.

    Schema validation passes it; json.dumps does not. If that failed after mkdir the
    empty directory would look like a real snapshot to the immutability guard and lock
    the commit out permanently.
    """
    runner.invoke(app, ["init"])
    doc = json.loads(json.dumps(example_results))
    doc["cases"][0]["metadata"] = {"obj": object()}

    with pytest.raises(ResultsFileError) as exc:
        create_snapshot(doc)
    assert "JSON-native" in str(exc.value)
    assert list((git_repo / ".drift" / "snapshots").iterdir()) == []

    # and the commit is still snapshottable once the bad value is gone
    assert create_snapshot(example_results).path.is_dir()
