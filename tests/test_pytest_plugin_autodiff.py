"""P7-J1: auto-diff inside the pytest plugin.

Two layers, tested separately:

- `_auto_diff_enabled` and `_auto_diff_lines` are pure functions with no git, no
  filesystem, no subprocess — direct unit tests against hand-built `CaseDiff`s.
- The full `pytest_sessionfinish` hook placement, exercised the same way
  `test_pytest_plugin.py` exercises the rest of the plugin: a real `pytest`
  subprocess against a throwaway repo. `_resolve_baseline` and `_write_reports` are
  monkeypatched via conftest.py the same way existing tests monkeypatch
  `create_snapshot` — the integration point under test here is the hook placement
  and the terminal block; `tests/test_nearest_ancestor_snapshot.py` and
  `tests/test_report.py` cover their real implementations.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from getdrift.diffing import CaseDiff, Comparability
from getdrift.pytest_plugin import (
    _auto_diff_enabled,
    _auto_diff_lines,
    _auto_export_enabled,
    _config_bool,
)


# --- pure unit tests: config/env plumbing --------------------------------------

def test_auto_diff_defaults_on_with_no_config_and_no_env(tmp_path, monkeypatch):
    monkeypatch.delenv("DRIFT_AUTO_DIFF", raising=False)
    assert _auto_diff_enabled(None, tmp_path) is True


def test_config_auto_diff_false_disables_it(tmp_path, monkeypatch):
    monkeypatch.delenv("DRIFT_AUTO_DIFF", raising=False)
    (tmp_path / "config.yaml").write_text("auto_diff: false\n")
    assert _auto_diff_enabled(None, tmp_path) is False


def test_config_auto_diff_quoted_false_string_also_disables_it(tmp_path, monkeypatch):
    """P8-A1: `_config_bool` — a YAML string `"false"` used to be treated as truthy
    (`"false" is not False`), so this opt-out silently did nothing. `auto_diff` is
    display-only, but the identical bug in `_auto_export_enabled` (below) means a
    user believing they had turned off disk writes had not."""
    monkeypatch.delenv("DRIFT_AUTO_DIFF", raising=False)
    (tmp_path / "config.yaml").write_text('auto_diff: "false"\n')
    assert _auto_diff_enabled(None, tmp_path) is False


def test_env_zero_disables_even_when_config_says_true(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text("auto_diff: true\n")
    monkeypatch.setenv("DRIFT_AUTO_DIFF", "0")
    assert _auto_diff_enabled(None, tmp_path) is False


def test_env_wins_over_config_false_too(tmp_path, monkeypatch):
    """Env overrides config in both directions, not just to disable."""
    (tmp_path / "config.yaml").write_text("auto_diff: false\n")
    monkeypatch.setenv("DRIFT_AUTO_DIFF", "1")
    assert _auto_diff_enabled(None, tmp_path) is True


def test_auto_export_defaults_on_with_no_config(tmp_path):
    assert _auto_export_enabled(tmp_path) is True


def test_auto_export_real_bool_false_disables_it(tmp_path):
    (tmp_path / "config.yaml").write_text("auto_export: false\n")
    assert _auto_export_enabled(tmp_path) is False


def test_auto_export_quoted_false_string_also_disables_it(tmp_path):
    """P8-A1: the actual bug this guards, not just `_auto_diff_enabled`'s copy of it
    — `auto_export` gates writing files to disk on every test run, unlike `auto_diff`
    which only prints. `is not False` treated the YAML string `"false"` as enabled;
    `write_reports` then ran anyway even though the user believed they had opted out."""
    (tmp_path / "config.yaml").write_text('auto_export: "false"\n')
    assert _auto_export_enabled(tmp_path) is False


@pytest.mark.parametrize("spelling", ["false", "False", "FALSE", "no", "off", "0", " false "])
def test_config_bool_recognises_every_falsy_spelling(spelling):
    assert _config_bool(spelling, default=True) is False


@pytest.mark.parametrize("value", ["true", "yes", "on", "1", "anything-else"])
def test_config_bool_treats_other_strings_as_truthy(value):
    assert _config_bool(value, default=False) is True


def test_config_bool_passes_a_real_bool_through_unchanged():
    assert _config_bool(True, default=False) is True
    assert _config_bool(False, default=True) is False


def test_config_bool_falls_back_to_default_when_key_is_absent():
    assert _config_bool(None, default=True) is True
    assert _config_bool(None, default=False) is False


def test_config_bool_coerces_an_unexpected_type_rather_than_raising():
    """P8-A1 brief: 'export_formats and auto_* keys receiving unexpected types rather
    than bools' — a list or an int must not crash config resolution."""
    assert _config_bool(0, default=True) is False
    assert _config_bool(1, default=False) is True
    assert _config_bool([], default=True) is False
    assert _config_bool(["golden_set"], default=False) is True


# --- pure unit tests: the compact terminal block --------------------------------

def _diff(case_id, bucket, **kw):
    kw.setdefault("pass_before", True)
    kw.setdefault("pass_after", True)
    kw.setdefault("score_before", 0.5)
    kw.setdefault("score_after", 0.5)
    kw.setdefault("delta", 0.0)
    kw.setdefault("shared_metrics", ["m"])
    return CaseDiff(case_id=case_id, bucket=bucket, **kw)


_BASELINE = "298b6827aa11bb22cc33dd44"


def _bucket_line(lines, bucket):
    """The block's line for `bucket` — leading spaces then the bucket name."""
    return next(l for l in lines if l.strip().startswith(bucket))


def test_block_opens_with_a_header_and_names_the_baseline():
    diffs = [_diff("a", "Unchanged")]
    lines = _auto_diff_lines(diffs, Comparability("equal", "v1", "v1", ""), _BASELINE)
    assert lines[0].startswith("── Drift")
    assert lines[1].strip() == f"vs {_BASELINE[:8]} (previous run, same branch)"
    assert lines[-1] == "─" * 40


def test_unchanged_is_a_count_only_never_names():
    diffs = [_diff(f"case_{i}", "Unchanged") for i in range(50)]
    comparability = Comparability("equal", "v1", "v1", "")
    lines = _auto_diff_lines(diffs, comparability, _BASELINE)
    assert _bucket_line(lines, "Unchanged").strip() == "Unchanged 50"
    assert "case_0" not in "\n".join(lines)


def test_regressed_degraded_fixed_new_are_named_when_nonzero():
    diffs = [
        _diff("a", "Regressed"),
        _diff("b", "Degraded"),
        _diff("c", "Fixed"),
        _diff("d", "New", pass_before=None, score_before=None, delta=None, shared_metrics=[]),
    ]
    comparability = Comparability("equal", "v1", "v1", "")
    lines = _auto_diff_lines(diffs, comparability, _BASELINE)
    for bucket, case_id in (("Regressed", "a"), ("Degraded", "b"), ("Fixed", "c"), ("New", "d")):
        assert case_id in _bucket_line(lines, bucket)


def test_improved_is_a_count_only_like_unchanged():
    diffs = [_diff("a", "Improved")]
    lines = _auto_diff_lines(diffs, Comparability("equal", "v1", "v1", ""), _BASELINE)
    assert _bucket_line(lines, "Improved").split() == ["Improved", "1"]


def test_noise_suppressed_case_gets_the_suppressed_marker():
    diffs = [_diff("flaky", "Unchanged", noise_filtered=True)]
    lines = _auto_diff_lines(diffs, Comparability("equal", "v1", "v1", ""), _BASELINE)
    assert any(l.strip().startswith("SUPPRESSED:") and "flaky" in l for l in lines)


def test_pass_flip_suppressed_case_gets_the_suppressed_marker_too():
    diffs = [_diff("flip", "Unchanged", pass_flip_filtered=True)]
    lines = _auto_diff_lines(diffs, Comparability("equal", "v1", "v1", ""), _BASELINE)
    assert any(
        l.strip().startswith("SUPPRESSED:") and "flip" in l and "majority" in l
        for l in lines
    )


def test_mismatch_suppresses_every_bucket_but_new_survives():
    """Same wording as drift diff's own MISMATCH handling — see diff_cmd.py:_uncomparable."""
    diffs = [
        _diff("a", "Regressed"),
        _diff("b", "New", pass_before=None, score_before=None, delta=None, shared_metrics=[]),
    ]
    comparability = Comparability("mismatch", "v1", "v2", "judge version changed from v1 to v2")
    lines = _auto_diff_lines(diffs, comparability, _BASELINE)
    assert any(
        l.strip() == "Not directly comparable — judge version changed from v1 to v2."
        for l in lines
    )
    assert any("suppressed" in l for l in lines)
    assert not any(l.strip().startswith("Regressed") for l in lines), \
        "a Regressed case must not appear once suppressed"
    assert any(l.strip().split(maxsplit=1) == ["New", "1: b"] for l in lines)


def test_unknown_prints_a_warning_but_still_shows_buckets():
    diffs = [_diff("a", "Unchanged")]
    comparability = Comparability(
        "unknown", None, None, "neither snapshot records a judge version"
    )
    lines = _auto_diff_lines(diffs, comparability, _BASELINE)
    assert any(l.strip().startswith("warning: neither snapshot records a judge version")
               for l in lines)
    assert _bucket_line(lines, "Unchanged").strip() == "Unchanged 1"


# --- end-to-end: the hook placement, with both deps stubbed --------------------

PLAIN_SUITE = "def test_alpha():\n    assert True\n"
REGRESSING_SUITE_2 = "def test_alpha():\n    assert False\n"


#: Absolute, computed once. A merely-inherited PYTHONPATH is often relative (this
#: suite is itself normally run as `PYTHONPATH=src pytest`) — fine for the outer
#: process, but silently wrong once passed to a child subprocess with a different
#: `cwd`: Python resolves a relative PYTHONPATH entry against the *child's* cwd, not
#: the one it was set in, so it points nowhere and the editable install (a checkout
#: elsewhere entirely) wins instead. Recomputing it absolute here is what makes these
#: subprocesses actually exercise this worktree's source rather than validating
#: nothing.
_SRC = str(Path(__file__).resolve().parent.parent / "src")


#: Set by the OUTER pytest process (this suite is itself run under pytest) and
#: otherwise inherited verbatim into the nested subprocess's env. Confirmed by
#: isolating it experimentally (present: the nested run silently reports the WRONG
#: test outcome for the still-running outer test's node id, every time; scrubbed:
#: correct, every time; scrubbing `PYTEST_VERSION` alongside it made no further
#: difference) — some part of pytest's own machinery keys off this var to decide
#: something about the run it's in, and inheriting the outer test's value into an
#: unrelated nested pytest process is exactly wrong. Must be scrubbed, not merely
#: overridden, since `env=` only adds/replaces keys, never removes inherited ones.
_LEAKS_FROM_OUTER_PYTEST = ("PYTEST_CURRENT_TEST",)


def _child_env(**overrides):
    """The env every subprocess in this file must use — not just the pytest ones.

    Any child process spawned from a test that is itself running under pytest needs
    the same three fixes, `drift init` included: it is a getdrift.cli invocation, and
    a relative PYTHONPATH resolved against the wrong cwd falls back to the shared
    venv's editable install just as easily for `init` as for a nested pytest run.
    """
    import os

    base_env = {k: v for k, v in os.environ.items() if k not in _LEAKS_FROM_OUTER_PYTEST}
    return {
        **base_env,
        "PYTHONPATH": _SRC,
        # These tests rewrite test_evals.py and re-run within the same test function,
        # often within the same filesystem mtime tick — a stale .pyc would then be
        # served instead of the new content. `-p no:cacheprovider` only disables
        # pytest's own cache plugin, not the interpreter's bytecode cache.
        "PYTHONDONTWRITEBYTECODE": "1",
        **overrides,
    }


def _run_pytest(cwd, env=None):
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=cwd, capture_output=True, text=True, env=_child_env(**(env or {})),
    )


def _commit(repo, message="c"):
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=repo, check=True)
    return _head(repo)


def _head(repo):
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()


_STUB_CONFTEST = '''
import os
import getdrift.pytest_plugin as plugin

def _fake_resolve(drift, exclude):
    return os.environ.get("TEST_BASELINE_HASH") or None

plugin._resolve_baseline = _fake_resolve
plugin._write_reports = lambda *a, **k: None
'''


@pytest.fixture()
def eval_repo(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_evals.py").write_text(PLAIN_SUITE)
    (tmp_path / "conftest.py").write_text(_STUB_CONFTEST)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for key, value in (("user.email", "t@example.com"), ("user.name", "T")):
        subprocess.run(["git", "config", key, value], cwd=tmp_path, check=True)
    _commit(tmp_path, "eval suite")
    subprocess.run(
        [sys.executable, "-m", "getdrift.cli", "init"], cwd=tmp_path, check=True,
        capture_output=True, env=_child_env(),
    )
    _commit(tmp_path, "drift init")
    return tmp_path


def test_no_ancestor_prints_nothing_not_even_a_notice(eval_repo):
    """Spec is explicit: no ancestor snapshot means silence, not a notice."""
    result = _run_pytest(eval_repo, env={"TEST_BASELINE_HASH": ""})

    assert result.returncode == 0
    assert "── Drift" not in result.stdout, "no ancestor means the whole block is absent"


def test_compact_block_appears_after_the_summary_line_on_a_regression(eval_repo):
    commit1 = _head(eval_repo)  # already passing, from the eval_repo fixture itself
    _run_pytest(eval_repo, env={"TEST_BASELINE_HASH": ""})

    (eval_repo / "tests" / "test_evals.py").write_text(REGRESSING_SUITE_2)
    _commit(eval_repo, "now failing")
    result = _run_pytest(eval_repo, env={"TEST_BASELINE_HASH": commit1})

    assert "Regressed 1: tests/test_evals.py::test_alpha" in result.stdout
    assert f"vs {commit1[:8]} (previous run, same branch)" in result.stdout
    lines = result.stdout.splitlines()
    # `pytest_terminal_summary` prints in the one place pytest hands a plugin the
    # terminal: after the FAILURES section (never interleaved into a traceback), and
    # before pytest's own two closing sections — "short test summary info" and the
    # final one-line footer ("1 failed in 0.06s"). Nothing runs after that footer at
    # all. This is the same slot pytest-cov's coverage table and pytest-benchmark's
    # results land in — it is the conventional, correct position for this hook, not
    # a corner case.
    failures_idx = next(i for i, line in enumerate(lines) if "FAILURES" in line)
    summary_idx = next(i for i, line in enumerate(lines) if "short test summary info" in line)
    block_idx = next(i for i, line in enumerate(lines) if line.startswith("── Drift"))
    assert failures_idx < block_idx < summary_idx, (
        "the block must come after the FAILURES section and before pytest's own "
        "short test summary — the position pytest_terminal_summary hooks print in"
    )


def test_exit_code_is_untouched_by_a_regression_finding(eval_repo):
    """Auto-diff reports; `drift ci` gates. This must never change pytest's own exit status."""
    commit1 = _head(eval_repo)  # already passing, from the eval_repo fixture itself
    # Only here to give commit1 a snapshot to diff against later — its own exit code
    # (0, a passing commit) is not part of what this test checks.
    _run_pytest(eval_repo, env={"TEST_BASELINE_HASH": "", "DRIFT_AUTO_DIFF": "0"})

    (eval_repo / "tests" / "test_evals.py").write_text(REGRESSING_SUITE_2)
    _commit(eval_repo, "now failing")
    with_finding = _run_pytest(eval_repo, env={"TEST_BASELINE_HASH": commit1})
    without_finding = _run_pytest(eval_repo, env={"TEST_BASELINE_HASH": commit1, "DRIFT_AUTO_DIFF": "0"})

    # Both runs exercise the SAME failing commit — only whether the auto-diff block
    # gets printed differs. The suite's own exit code (1, `assert False`) must be
    # identical either way; auto-diff reports, `drift ci` gates.
    assert with_finding.returncode == without_finding.returncode == 1
    assert "── Drift" in with_finding.stdout
    assert "── Drift" not in without_finding.stdout


def test_drift_auto_diff_env_zero_suppresses_the_block(eval_repo):
    commit1 = _head(eval_repo)  # already passing, from the eval_repo fixture itself
    _run_pytest(eval_repo, env={"TEST_BASELINE_HASH": ""})

    (eval_repo / "tests" / "test_evals.py").write_text(REGRESSING_SUITE_2)
    _commit(eval_repo, "now failing")
    result = _run_pytest(eval_repo, env={"TEST_BASELINE_HASH": commit1, "DRIFT_AUTO_DIFF": "0"})

    assert "── Drift" not in result.stdout


def test_a_resolver_failure_degrades_to_silence_not_a_broken_suite(eval_repo):
    """P7-D1's resolver, or anything else in the auto-diff path, may not break the host suite."""
    (eval_repo / "conftest.py").write_text('''
import getdrift.pytest_plugin as plugin

def _explode(drift, exclude):
    raise RuntimeError("no ancestry index yet")

plugin._resolve_baseline = _explode
plugin._write_reports = lambda *a, **k: None
''')
    result = _run_pytest(eval_repo)

    assert result.returncode == 0
    assert "Drift: snapshot written" in result.stdout
    assert "Traceback" not in result.stdout


def test_a_report_writing_failure_also_degrades_to_silence(eval_repo):
    """An unreadable report dir must not leave a half-printed block — see docstring."""
    commit1 = _head(eval_repo)  # already passing, from the eval_repo fixture itself
    _run_pytest(eval_repo, env={"TEST_BASELINE_HASH": ""})

    (eval_repo / "tests" / "test_evals.py").write_text(REGRESSING_SUITE_2)
    _commit(eval_repo, "now failing")
    (eval_repo / "conftest.py").write_text('''
import os
import getdrift.pytest_plugin as plugin

def _fake_resolve(drift, exclude):
    return os.environ.get("TEST_BASELINE_HASH")

def _explode(*a, **k):
    raise OSError("report dir unreadable")

plugin._resolve_baseline = _fake_resolve
plugin._write_reports = _explode
''')
    result = _run_pytest(eval_repo, env={"TEST_BASELINE_HASH": commit1})

    assert result.returncode == 1  # the suite's own failure, untouched
    assert "── Drift" not in result.stdout
    assert "Traceback" not in result.stdout
