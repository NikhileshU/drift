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


SKIP_SUITE = '''
import pytest

def test_plain_pass():
    assert True

@pytest.mark.skip(reason="decorator form")
def test_decorator_skip():
    assert True

def test_runtime_skip():
    pytest.skip("runtime form")

@pytest.mark.xfail(reason="known regression")
def test_xfail():
    assert False

@pytest.mark.xfail(reason="marker left behind")
def test_xpass():
    assert True
'''


def test_both_skip_mechanisms_are_excluded(eval_repo):
    """A skipped test produced no verdict, whichever way it was skipped.

    Recording only the runtime form made ADDING a `pytest.skip()` show up as Regressed
    and removing it as Fixed — the exact false signal Drift exists to suppress.
    """
    (eval_repo / "tests" / "test_evals.py").write_text(SKIP_SUITE)
    commit = _init_drift(eval_repo)
    _run_pytest(eval_repo)

    cases = {c["case_id"].split("::")[1]: c for c in _snapshot(eval_repo, commit)["cases"]}
    assert "test_decorator_skip" not in cases
    assert "test_runtime_skip" not in cases, "a runtime pytest.skip() is still a skip"
    # xfail is not a skip: it ran and it failed, so it stays visible as a failing case
    # rather than silently vanishing from the diff.
    assert cases["test_xfail"]["pass"] is False
    assert cases["test_xpass"]["pass"] is True
    assert cases["test_plain_pass"]["pass"] is True


def test_a_non_benign_snapshot_error_is_loud(eval_repo):
    """Only SnapshotExistsError is benign. A policy rejection must not look routine."""
    (eval_repo / "conftest.py").write_text('''
import getdrift.pytest_plugin as plugin
from getdrift.snapshot import SnapshotError

class MissingJudgeVersionError(SnapshotError):
    pass

def _refuse(*args, **kwargs):
    raise MissingJudgeVersionError("judge_version is required by .drift/config.yaml")

plugin.create_snapshot = _refuse
''')
    _init_drift(eval_repo)
    result = _run_pytest(eval_repo)

    assert result.returncode == 0, "a refused snapshot must not fail the suite"
    assert "no snapshot written" in result.stdout
    assert "judge_version is required" in result.stdout


def test_warning_filters_cannot_fail_the_suite(eval_repo):
    """Under -W error, warnings.warn in session teardown would break a passing run."""
    (eval_repo / "conftest.py").write_text('''
import getdrift.pytest_plugin as plugin
from getdrift.snapshot import SnapshotError

def _refuse(*args, **kwargs):
    raise SnapshotError("refused")

plugin.create_snapshot = _refuse
''')
    _init_drift(eval_repo)
    result = _run_pytest(eval_repo, "-W", "error")

    assert result.returncode == 0
    assert "no snapshot written" in result.stdout, "still visible despite the filter"


def test_an_unserialisable_record_property_value_costs_nothing(eval_repo):
    """record_property takes any object; reaching json.dumps would break the run."""
    (eval_repo / "tests" / "test_evals.py").write_text('''
class Thing:
    pass

def test_ok(record_property):
    record_property("drift.metadata.obj", Thing())
    assert True
''')
    commit = _init_drift(eval_repo)
    result = _run_pytest(eval_repo)

    assert result.returncode == 0
    case = _snapshot(eval_repo, commit)["cases"][0]
    # Coerced, not dropped — the value is still worth having.
    assert "Thing object" in case["metadata"]["obj"]


def test_an_unexpected_error_never_fails_a_passing_suite(eval_repo):
    """The contract's last line: a snapshot must never break the suite it observes."""
    (eval_repo / "conftest.py").write_text('''
import getdrift.pytest_plugin as plugin

def _explode(*args, **kwargs):
    raise RuntimeError("something nobody predicted")

plugin.create_snapshot = _explode
''')
    _init_drift(eval_repo)
    result = _run_pytest(eval_repo)

    assert result.returncode == 0
    assert "unexpected RuntimeError" in result.stdout
