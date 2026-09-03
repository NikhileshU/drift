"""The importable API other packages call — no Typer, no subprocess."""
import json
import subprocess
from pathlib import Path

import pytest

from getdrift.gitutil import GitError, head_hash
from getdrift.paths import drift_dir
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


def test_snapshot_directory_is_invisible_until_fully_written(git_repo, monkeypatch):
    """P6-D1: writes go through a temp dir published by a single os.replace, so a
    concurrent reader (a parallel CI runner, `drift log`, load_history()) must never
    see `target` before every file in it exists. Spy on every write to prove it."""
    runner.invoke(app, ["init"])
    target = drift_dir() / "snapshots" / head_hash()

    seen_target_exists = []
    real_write_text = Path.write_text

    def spy(self, *args, **kwargs):
        seen_target_exists.append(target.exists())
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", spy)
    snap = create_snapshot(DEMO / "baseline.json")

    assert seen_target_exists, "write_text was never called — nothing was exercised"
    assert not any(seen_target_exists), "target was visible before every file was written"
    assert snap.path == target
    assert target.is_dir()  # published after the fact, by the single os.replace


def test_mid_write_failure_leaves_no_temp_directory_behind(git_repo, monkeypatch):
    """Same failure-ordering reasoning as the unserialisable-metadata test above, one
    step later: a write that fails partway through (not on the first file) must still
    leave nothing behind — now in the temp dir the write actually happens in."""
    runner.invoke(app, ["init"])
    base = drift_dir()

    calls = {"n": 0}
    real_write_text = Path.write_text

    def flaky(self, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:  # results.json already written; manifest.json fails
            raise OSError("disk full (simulated)")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", flaky)
    with pytest.raises(OSError):
        create_snapshot(DEMO / "baseline.json")

    for name in ("snapshots", ".tmp"):
        directory = base / name
        leftovers = list(directory.iterdir()) if directory.is_dir() else []
        assert leftovers == [], f"{name}/ has leftovers: {leftovers}"
