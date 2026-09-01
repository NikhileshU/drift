import json
import subprocess

from typer.testing import CliRunner

from getdrift.cli import app

runner = CliRunner()


def _init(repo):
    runner.invoke(app, ["init"])
    return repo / ".drift"


def _write(repo, name, document):
    path = repo / name
    path.write_text(json.dumps(document))
    return path


def _head(repo):
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()


def test_snapshot_writes_manifest_and_results(git_repo, example_results):
    drift = _init(git_repo)
    path = _write(git_repo, "r.json", example_results)
    result = runner.invoke(app, ["snapshot", "--results-file", str(path)])
    assert result.exit_code == 0, result.output

    snapshot = drift / "snapshots" / _head(git_repo)
    assert json.loads((snapshot / "results.json").read_text()) == example_results
    manifest = json.loads((snapshot / "manifest.json").read_text())
    assert manifest["commit_hash"] == _head(git_repo)
    assert manifest["case_count"] == len(example_results["cases"])
    assert manifest["judge_version"] == "unset"


def test_manifest_versions_come_from_flags(git_repo, example_results):
    drift = _init(git_repo)
    path = _write(git_repo, "r.json", example_results)
    runner.invoke(
        app,
        [
            "snapshot",
            "--results-file", str(path),
            "--model-version", "claude-opus-5",
            "--prompt-version", "agent@v7",
            "--judge-version", "rubric@3ab91f",
        ],
    )
    manifest = json.loads(
        (drift / "snapshots" / _head(git_repo) / "manifest.json").read_text()
    )
    assert manifest["model_version"] == "claude-opus-5"
    assert manifest["prompt_version"] == "agent@v7"
    assert manifest["judge_version"] == "rubric@3ab91f"


def test_invalid_results_are_rejected_loudly(git_repo, invalid_results):
    drift = _init(git_repo)
    path = _write(git_repo, "bad.json", invalid_results)
    result = runner.invoke(app, ["snapshot", "--results-file", str(path)])
    assert result.exit_code == 1
    assert "does not conform" in result.output
    assert "duplicate case_id" in result.output
    assert not (drift / "snapshots" / _head(git_repo)).exists()


def test_malformed_json_is_rejected(git_repo):
    _init(git_repo)
    path = git_repo / "bad.json"
    path.write_text("{not json")
    result = runner.invoke(app, ["snapshot", "--results-file", str(path)])
    assert result.exit_code == 1
    assert "not valid JSON" in result.output


def test_missing_results_file_is_rejected(git_repo):
    _init(git_repo)
    result = runner.invoke(app, ["snapshot", "--results-file", "nope.json"])
    assert result.exit_code == 1
    assert "does not exist" in result.output


def test_snapshot_requires_drift_init(git_repo, example_results):
    path = _write(git_repo, "r.json", example_results)
    result = runner.invoke(app, ["snapshot", "--results-file", str(path)])
    assert result.exit_code == 1
    assert "drift init" in result.output


# --- D3d: the immutability guarantee -----------------------------------------


def test_existing_snapshot_is_never_overwritten(git_repo, example_results):
    """The core guarantee: a second snapshot of the same commit is a hard error."""
    drift = _init(git_repo)
    path = _write(git_repo, "r.json", example_results)
    assert runner.invoke(app, ["snapshot", "--results-file", str(path)]).exit_code == 0

    snapshot = drift / "snapshots" / _head(git_repo)
    before = (snapshot / "results.json").read_text()

    changed = json.loads(json.dumps(example_results))
    changed["cases"][0]["pass"] = False
    second = _write(git_repo, "r2.json", changed)

    result = runner.invoke(app, ["snapshot", "--results-file", str(second)])
    assert result.exit_code == 1
    assert "already exists" in result.output
    assert "immutable" in result.output
    assert (snapshot / "results.json").read_text() == before


def test_no_force_flag_exists_to_defeat_immutability(git_repo):
    result = runner.invoke(app, ["snapshot", "--help"])
    assert "--force" not in result.output


def test_a_new_commit_gets_its_own_snapshot(git_repo, example_results):
    drift = _init(git_repo)
    path = _write(git_repo, "r.json", example_results)
    runner.invoke(app, ["snapshot", "--results-file", str(path)])
    first = _head(git_repo)

    (git_repo / "README.md").write_text("changed\n")
    subprocess.run(["git", "commit", "-aqm", "second"], cwd=git_repo, check=True)

    assert runner.invoke(app, ["snapshot", "--results-file", str(path)]).exit_code == 0
    second = _head(git_repo)
    assert first != second
    assert (drift / "snapshots" / first / "manifest.json").exists()
    assert (drift / "snapshots" / second / "manifest.json").exists()


def test_dirty_tree_snapshots_but_warns(git_repo, example_results):
    _init(git_repo)
    path = _write(git_repo, "r.json", example_results)
    (git_repo / "README.md").write_text("uncommitted\n")
    result = runner.invoke(app, ["snapshot", "--results-file", str(path)])
    assert result.exit_code == 0
    assert "uncommitted changes" in result.output


def test_a_real_score_discrepancy_is_recorded_in_the_snapshot(git_repo):
    """D1: a warning scrolls past, but an immutable snapshot outlives the terminal."""
    from typer.testing import CliRunner

    from getdrift.cli import app

    runner = CliRunner()
    runner.invoke(app, ["init"])
    results = {
        "schema_version": "1.1.0",
        "cases": [{
            "case_id": "c",
            "metric_scores": {"accuracy": 0.90},  # the runs average 0.50
            "pass": True,
            "environment": "golden_set",
            "timestamp": "2026-09-01T09:41:02Z",
            "runs": [
                {"metric_scores": {"accuracy": 0.40}, "pass": True},
                {"metric_scores": {"accuracy": 0.60}, "pass": True},
            ],
        }],
    }
    path = git_repo / "results.json"
    path.write_text(json.dumps(results))
    result = runner.invoke(app, ["snapshot", "--results-file", str(path)])
    assert result.exit_code == 0

    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, capture_output=True, text=True
    ).stdout.strip()
    written = json.loads(
        (git_repo / ".drift" / "snapshots" / commit / "results.json").read_text()
    )
    recorded = written["cases"][0]["metadata"]["drift"]["metric_scores_discrepancy"]
    assert recorded["accuracy"]["reported"] == 0.90
    assert recorded["accuracy"]["runs_mean"] == 0.50


def test_rounding_a_summary_score_is_not_a_discrepancy(git_repo):
    """A harness printing three decimals must not warn on every single case."""
    from typer.testing import CliRunner

    from getdrift.cli import app

    runner = CliRunner()
    runner.invoke(app, ["init"])
    results = {
        "schema_version": "1.1.0",
        "cases": [{
            "case_id": "c",
            "metric_scores": {"accuracy": 0.667},  # true mean 0.6666...
            "pass": True,
            "environment": "golden_set",
            "timestamp": "2026-09-01T09:41:02Z",
            "runs": [
                {"metric_scores": {"accuracy": 0.6}, "pass": True},
                {"metric_scores": {"accuracy": 0.7}, "pass": True},
                {"metric_scores": {"accuracy": 0.7}, "pass": True},
            ],
        }],
    }
    path = git_repo / "results.json"
    path.write_text(json.dumps(results))
    result = runner.invoke(app, ["snapshot", "--results-file", str(path)])
    assert "disagree with the mean" not in result.output
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=git_repo, capture_output=True, text=True
    ).stdout.strip()
    written = json.loads(
        (git_repo / ".drift" / "snapshots" / commit / "results.json").read_text()
    )
    assert "metadata" not in written["cases"][0]
