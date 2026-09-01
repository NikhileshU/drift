"""J9: the pytest plugin, exercised the way a user gets it — installed, then `pytest`.

These run pytest in a subprocess against throwaway repos rather than with the
`pytester` fixture, because the claim being tested is precisely that the entry point
does the work with no conftest, no flag and no import in the user's files.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

PLAIN_SUITE = '''
def test_alpha():
    assert True

def test_beta():
    assert True
'''


def _run_pytest(cwd, *args):
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *args],
        cwd=cwd, capture_output=True, text=True,
    )


def _commit(repo, message="c"):
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture()
def eval_repo(tmp_path):
    """A git repo with an eval suite that contains no reference to Drift at all."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_evals.py").write_text(PLAIN_SUITE)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for key, value in (("user.email", "t@example.com"), ("user.name", "T")):
        subprocess.run(["git", "config", key, value], cwd=tmp_path, check=True)
    _commit(tmp_path, "eval suite")
    return tmp_path


def _init_drift(repo):
    """`drift init` is project setup, not a change to any test file."""
    subprocess.run([sys.executable, "-m", "getdrift.cli", "init"], cwd=repo, check=True,
                   capture_output=True)
    return _commit(repo, "drift init")


def _snapshot(repo, commit):
    return json.loads((repo / ".drift" / "snapshots" / commit / "results.json").read_text())


def test_pip_install_alone_snapshots_a_suite_with_no_test_file_changes(eval_repo):
    """J9c: the whole deliverable. No conftest, no import, no flag — just `pytest`."""
    commit = _init_drift(eval_repo)
    result = _run_pytest(eval_repo)

    assert result.returncode == 0
    assert "Drift: snapshot written" in result.stdout
    results = _snapshot(eval_repo, commit)
    assert [case["case_id"] for case in results["cases"]] == [
        "tests/test_evals.py::test_alpha",
        "tests/test_evals.py::test_beta",
    ]
    # `passed` is always present so any suite satisfies the schema's >=1 metric rule.
    assert all(case["metric_scores"] == {"passed": 1.0} for case in results["cases"])
    assert results["metadata"]["harness"] == "pytest"


def test_record_property_reports_scores_without_importing_drift(eval_repo):
    (eval_repo / "tests" / "test_evals.py").write_text('''
def test_scored(record_property):
    record_property("drift.score.answer_correctness", 0.91)
    record_property("drift.metadata.trace_id", "abc123")
    record_property("drift.case_id", "refund_policy_multi_turn")
    assert True
''')
    commit = _init_drift(eval_repo)
    _run_pytest(eval_repo)

    case = _snapshot(eval_repo, commit)["cases"][0]
    assert case["case_id"] == "refund_policy_multi_turn"
    assert case["metric_scores"] == {"passed": 1.0, "answer_correctness": 0.91}
    assert case["metadata"]["trace_id"] == "abc123"


def test_a_failing_run_is_still_snapshotted(eval_repo):
    """The failures are exactly what the next diff needs to see get fixed."""
    (eval_repo / "tests" / "test_evals.py").write_text("def test_broken():\n    assert False\n")
    commit = _init_drift(eval_repo)
    result = _run_pytest(eval_repo)

    assert result.returncode == 1, "the suite must still report its own failure"
    case = _snapshot(eval_repo, commit)["cases"][0]
    assert case["pass"] is False and case["metric_scores"]["passed"] == 0.0


def test_repo_without_drift_init_is_a_silent_noop(eval_repo):
    """Having Drift installed must never disturb a suite that does not use it."""
    result = _run_pytest(eval_repo)

    assert result.returncode == 0
    assert "Drift" not in result.stdout
    assert not (eval_repo / ".drift").exists()


def test_rerunning_on_an_unchanged_commit_is_benign(eval_repo):
    """Snapshots are immutable; re-running pytest without committing is normal."""
    _init_drift(eval_repo)
    first = _run_pytest(eval_repo)
    second = _run_pytest(eval_repo)

    assert first.returncode == 0 and second.returncode == 0
    assert "already exists" in second.stdout
    assert "error" not in second.stdout.lower()


def test_no_drift_snapshot_flag_disables_it(eval_repo):
    commit = _init_drift(eval_repo)
    result = _run_pytest(eval_repo, "--no-drift-snapshot")

    assert result.returncode == 0
    assert not (eval_repo / ".drift" / "snapshots" / commit).exists()


def test_provenance_flags_reach_the_manifest(eval_repo):
    commit = _init_drift(eval_repo)
    _run_pytest(eval_repo, "--drift-judge-version", "rubric@v3",
                "--drift-model-version", "claude-opus-5")

    manifest = json.loads(
        (eval_repo / ".drift" / "snapshots" / commit / "manifest.json").read_text()
    )
    assert manifest["judge_version"] == "rubric@v3"
    assert manifest["model_version"] == "claude-opus-5"
    assert manifest["prompt_version"] == "unset"
