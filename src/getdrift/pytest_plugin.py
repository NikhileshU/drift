"""Drift's pytest plugin: snapshot a pytest-based eval suite with no test-file changes.

Registered as a `pytest11` entry point, so `pip install getdrift` is the whole install.
It activates only in a repo that has run `drift init` — without `.drift/` it no-ops
silently, so having Drift on the environment never changes an unrelated suite's run.

Every test that runs becomes an eval case keyed by its pytest node id. Scores beyond
pass/fail are optional and are read from `record_property`, pytest's own builtin
fixture, so a suite can report them without importing Drift.
"""

import json
import os
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from getdrift.diffing import (
    BUCKET_ORDER,
    SUPPRESSED_MARKER,
    UNKNOWN,
    CaseDiff,
    Comparability,
    compare,
    judge_comparability,
)
from getdrift.gitutil import GitError
from getdrift.paths import read_config
from getdrift.schema import SCHEMA_VERSION, SchemaValidationError
from getdrift.snapshot import (
    NotInitializedError,
    Snapshot,
    SnapshotError,
    SnapshotExistsError,
    create_snapshot,
    load_snapshot,
    nearest_ancestor_snapshot,
)

SCORE_PREFIX = "drift.score."
METADATA_PREFIX = "drift.metadata."
CASE_ID = "drift.case_id"
ENVIRONMENT = "drift.environment"

#: exitstatus values where the collected cases would be an incomplete picture of the
#: suite. 0 (all passed) and 1 (tests failed) are both snapshotted — a regression tool
#: that only records green runs is useless.
INCOMPLETE = {2, 3, 4, 5}  # interrupted, internal error, usage error, no tests collected

#: Manifest provenance fields, passed straight through to create_snapshot().
VERSIONS = (
    ("model_version", "Model under test, recorded in the snapshot manifest."),
    ("prompt_version", "Prompt / agent config version."),
    ("judge_version", "Scoring rubric / judge version. `drift diff` compares this "
                      "between snapshots to decide whether their scores are comparable."),
)
_OPTIONS = VERSIONS + (("environment", "golden_set or production_sample."),)


def pytest_addoption(parser):
    group = parser.getgroup("drift", "Drift eval snapshots")
    group.addoption(
        "--no-drift-snapshot",
        dest="drift_snapshot",
        action="store_false",
        default=True,
        help="Do not write a Drift snapshot after this run.",
    )
    for name, help_text in _OPTIONS:
        group.addoption(f"--drift-{name.replace('_', '-')}", dest=f"drift_{name}", default=None, help=help_text)
        # Same settings from pytest.ini / pyproject.toml, so a repo can set them once.
        parser.addini(f"drift_{name}", help_text, default=None)


def _setting(config, name: str) -> Any:
    return config.getoption(f"drift_{name}") or config.getini(f"drift_{name}")


def _iso(epoch_seconds: float) -> str:
    return (
        datetime.fromtimestamp(epoch_seconds, timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _jsonable(value: Any) -> Any:
    """`record_property` accepts any object, but results.json has to be JSON.

    Coerce rather than drop: an unserialisable value is worth keeping as its repr, and
    letting it reach json.dumps would raise inside session teardown and break a suite
    that had otherwise passed.
    """
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return repr(value)
    return value


def _case(report, environment: str) -> Dict[str, Any]:
    """One pytest report as a results.json case entry.

    `case_id` is the node id — a path plus a test name, which is exactly the kind of
    durable identifier the schema asks for, and stable across commits unless the test
    is renamed or moved.
    """
    properties = dict(getattr(report, "user_properties", []) or [])
    passed = report.outcome == "passed"

    # `passed` is always present so any suite is snapshottable with no test changes at
    # all; the schema requires at least one metric. Richer scores are merged over it.
    scores = {"passed": 1.0 if passed else 0.0}
    for key, value in properties.items():
        if key.startswith(SCORE_PREFIX) and isinstance(value, (int, float)) and not isinstance(value, bool):
            scores[key[len(SCORE_PREFIX):]] = value

    metadata = {
        key[len(METADATA_PREFIX):]: _jsonable(value)
        for key, value in properties.items()
        if key.startswith(METADATA_PREFIX)
    }
    if hasattr(report, "wasxfail"):
        # Explains why an xfail case reads pass=False with outcome "skipped".
        metadata.setdefault("xfail", report.wasxfail or True)
    metadata.setdefault("outcome", report.outcome)
    metadata.setdefault("duration_s", round(report.duration, 4))

    return {
        "case_id": str(properties.get(CASE_ID) or report.nodeid),
        "metric_scores": scores,
        "pass": passed,
        "environment": str(properties.get(ENVIRONMENT) or environment),
        "timestamp": _iso(report.stop),
        "metadata": metadata,
    }


def _is_skip(report) -> bool:
    """Whether the test produced no verdict at all, so it is not an eval case.

    Both skip mechanisms have to agree here. `@pytest.mark.skip` skips during setup,
    while a runtime `pytest.skip()` skips during the call phase — counting only one of
    them as a failure would make ADDING a skip show up as Regressed and removing it as
    Fixed, which is the exact false signal Drift exists to suppress.

    xfail is deliberately not a skip: pytest reports it as skipped, but the test really
    ran and really failed, so it stays in the snapshot as a failing case. Dropping it
    would make a known-failing eval silently vanish from the diff.
    """
    return report.skipped and not hasattr(report, "wasxfail")


class _Collector:
    def __init__(self, environment: str) -> None:
        self.environment = environment
        self.cases: List[Dict[str, Any]] = []

    def pytest_runtest_logreport(self, report) -> None:
        """Collect finished tests. A setup failure counts: a case that could not run failed."""
        if _is_skip(report):
            return
        if report.when == "call" or (report.when == "setup" and report.failed):
            self.cases.append(_case(report, self.environment))


def pytest_configure(config):
    config._drift = _Collector(_setting(config, "environment") or "golden_set")
    config.pluginmanager.register(config._drift, "drift-collector")


def pytest_sessionfinish(session, exitstatus):
    config = session.config
    collector = getattr(config, "_drift", None)

    # A snapshot must never break the suite it is observing, so every failure below is
    # a warning. Nothing here is worth failing a green test run over.
    if collector is None or not config.getoption("drift_snapshot"):
        return
    if hasattr(config, "workerinput"):
        return  # an xdist worker; the controller writes the one snapshot
    if int(exitstatus) in INCOMPLETE or not collector.cases:
        return

    results = {
        "schema_version": SCHEMA_VERSION,
        "cases": collector.cases,
        "metadata": {"harness": "pytest", "exit_status": int(exitstatus)},
    }
    versions = {name: _setting(config, name) for name, _ in VERSIONS}
    try:
        snapshot = create_snapshot(results, **{k: v for k, v in versions.items() if v})
    except NotInitializedError:
        return  # this repo does not use Drift; say nothing
    except SnapshotExistsError as exc:
        # Benign, and only this one: re-running pytest on an unchanged commit is normal.
        _report(config, str(exc))
        return
    except (SnapshotError, GitError, SchemaValidationError) as exc:
        # Every other SnapshotError means the user wanted a snapshot and did not get
        # one — a judge-version policy rejection, say. Printing it quietly like the
        # benign case would let an opted-in team lose snapshots without noticing.
        _warn(config, f"no snapshot written — {exc}")
        return
    except Exception as exc:  # noqa: BLE001 - see below
        # Deliberately broad, and the last line of the contract: a regression tool must
        # never break the suite it observes. Anything unforeseen here costs the user a
        # snapshot, which is recoverable; letting it out of pytest_sessionfinish costs
        # them a green test run, which is not.
        _warn(config, f"no snapshot written — unexpected {type(exc).__name__}: {exc}")
        return

    _report(config, f"snapshot written: {snapshot.path} ({len(collector.cases)} case(s))")
    # Stashed rather than diffed right here: pytest_sessionfinish runs interleaved
    # with other plugins' own sessionfinish hooks, including the terminalreporter's,
    # so anything printed from here can land before test output is even done —
    # `pytest_terminal_summary` is pytest's dedicated hook for a plugin's own summary
    # section instead: it fires after the FAILURES section, in the same slot
    # pytest-cov's coverage table lands in. See `_auto_diff` below.
    config._drift_snapshot = snapshot


def _report(config, message: str) -> None:
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_sep("-", f"Drift: {message}")


def _warn(config, message: str) -> None:
    """Surface a problem loudly without any way to fail the run.

    The terminal line is the reliable half: `warnings.warn` is subject to the repo's
    filters, and under `-W error` it would raise inside session teardown — turning a
    missing snapshot into a failed test run.
    """
    _report(config, message)
    try:
        warnings.warn(f"Drift: {message}", stacklevel=2)
    except Exception:  # noqa: BLE001 - a warning filter must not fail the suite
        pass


# --- P7-J1: auto-diff against the nearest ancestor snapshot -------------------------
#
# Runs after a snapshot is written. Reports only — never gates. `drift ci` is the
# thing CI fails on; this exists so a human sees "you just regressed X" without
# leaving the terminal they were already looking at.

AUTO_DIFF_ENV = "DRIFT_AUTO_DIFF"

#: Bucket names for which a nonzero count also lists the case ids. New coverage and
#: anything that actually changed a verdict is worth naming; Improved/Unchanged are
#: not — at N cases either could be the whole suite, and that is what `drift diff`
#: is for. Matches the spec: "Case names listed ONLY for non-zero Fixed/Regressed/
#: Degraded/New."
NAMED_BUCKETS = {"Fixed", "Regressed", "Degraded", "New"}


def _auto_diff_enabled(config, drift: Path) -> bool:
    """Env wins over config; config's own default is on.

    `DRIFT_AUTO_DIFF=0` disables regardless of config.yaml. Any other env value, or
    no env var at all, falls through to `auto_diff` in config.yaml, which defaults to
    on — only an explicit `auto_diff: false` turns it off.
    """
    env = os.environ.get(AUTO_DIFF_ENV)
    if env is not None:
        return env.strip() != "0"
    return read_config(drift).get("auto_diff", True) is not False


def _names(cases: List[CaseDiff]) -> str:
    return ", ".join(sorted(c.case_id for c in cases))


#: A boxed block, not six `Drift: `-prefixed repetitions: the header alone carries the
#: "this is Drift" context, so the reader scans an aligned block instead of the same
#: six-character prefix six times. Width is cosmetic, not a contract — nothing parses
#: it — chosen only to roughly match the spec's own quoted example.
_BLOCK_WIDTH = 40
_HEADER = ("── Drift " + "─" * _BLOCK_WIDTH)[:_BLOCK_WIDTH]
_FOOTER = "─" * _BLOCK_WIDTH
#: Longest bucket name ("Regressed"/"Unchanged") plus one column of breathing room,
#: so every count lines up under the next regardless of which bucket it belongs to.
_LABEL_WIDTH = max(len(b) for b in BUCKET_ORDER) + 1


def _bucket_line(bucket: str, cases: List[CaseDiff]) -> str:
    line = f"  {bucket.ljust(_LABEL_WIDTH)}{len(cases)}"
    if bucket in NAMED_BUCKETS and cases:
        line += f": {_names(cases)}"
    return line


def _auto_diff_lines(
    diffs: List[CaseDiff], comparability: Comparability, baseline_hash: str
) -> List[str]:
    """The compact terminal block, as plain lines — no colour, this is pytest output.

    Opens with the baseline it compared against: a verdict is meaningless without
    knowing what it is a verdict against, and unlike `drift diff` (baseline and
    candidate both named on the command line) this path never shows it anywhere else
    the reader is looking. Mirrors `drift diff`'s own MISMATCH/UNKNOWN wording
    verbatim (the spec's instruction): a team should not learn two different
    sentences for the same fact depending on whether they read it here or ran
    `drift diff` by hand.
    """
    lines = [_HEADER, f"  vs {baseline_hash[:8]} (previous run, same branch)", ""]

    if comparability.suppresses_verdicts:
        fresh = [c for c in diffs if c.bucket == "New"]
        lines += [
            f"  Not directly comparable — {comparability.detail}.",
            "  Fixed / Regressed / Improved / Degraded / Unchanged are suppressed: "
            "a verdict on these deltas would be about the rubric, not the model.",
            "",
            _bucket_line("New", fresh),
            _FOOTER,
        ]
        return lines

    if comparability.state == UNKNOWN:
        lines.append(
            f"  warning: {comparability.detail}. The verdicts below are unverified "
            "— pass --judge-version to `drift snapshot` so Drift can check them."
        )
        lines.append("")

    for bucket in BUCKET_ORDER:
        cases = [c for c in diffs if c.bucket == bucket]
        lines.append(_bucket_line(bucket, cases))

    noisy = [c for c in diffs if c.noise_filtered]
    flips = [c for c in diffs if c.pass_flip_filtered]
    suppressed = [
        (cases, reason)
        for cases, reason in (
            (noisy, "moved past the threshold but stayed inside the noise floor"),
            (flips, "had a pass flip that did not survive the majority across runs"),
        )
        if cases
    ]
    if suppressed:
        lines.append("")
        for cases, reason in suppressed:
            lines.append(
                f"  {SUPPRESSED_MARKER} {len(cases)} case(s) {reason}: {_names(cases)}"
            )

    lines.append(_FOOTER)
    return lines


def _resolve_baseline(drift: Path, exclude: str) -> Optional[str]:
    """The nearest-ancestor snapshot's commit hash, or None if there is not one.

    Thin wrapper over `nearest_ancestor_snapshot` (P7-D1) so tests can monkeypatch it
    without touching git — same reasoning as `_write_reports` below. `exclude` is
    passed as `commit`: the ancestry walk to search from, which for auto-diff is
    always the commit that was just snapshotted (usually HEAD, but explicit here
    rather than relying on that default — a pytest run against a detached, non-HEAD
    commit should still diff from the right place).
    """
    return nearest_ancestor_snapshot(commit=exclude, drift=drift)


#: Whether `write_reports` runs at all. A second, independent switch from
#: `auto_diff`: the terminal block is transient, gone when the log scrolls past — a
#: written report file persists on disk on every single test run, which is a bigger
#: default footprint to opt someone into. Config-only (no env override): unlike
#: auto_diff, nothing in the brief asked for one, and a second env var doubles the
#: surface for no requirement behind it — add one if a real need shows up.
AUTO_EXPORT_CONFIG_KEY = "auto_export"


def _auto_export_enabled(drift: Path) -> bool:
    return read_config(drift).get(AUTO_EXPORT_CONFIG_KEY, True) is not False


def _write_reports(
    diffs: List[CaseDiff],
    comparability: Comparability,
    baseline_hash: str,
    candidate_hash: str,
    created_at: str,
    removed: List[str],
    drift: Path,
) -> None:
    """Thin wrapper so tests can monkeypatch this without report.py needing to exist.

    P7-A1 (Angela): src/getdrift/report.py. Final signature: `write_reports(diffs,
    comparability, baseline_hash, candidate_hash, created_at, removed=(), drift=None,
    formats=("json", "md")) -> List[Path]`. Imported lazily, at call time, so this
    module loads fine before report.py exists — and so that a missing or broken
    report.py degrades to "no report written, nothing printed" rather than an
    ImportError at plugin registration that would break every suite using Drift at
    all, not just the auto-diff feature.
    """
    from getdrift.report import write_reports

    write_reports(
        diffs, comparability, baseline_hash, candidate_hash, created_at,
        removed=removed, drift=drift,
    )


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    """Print the auto-diff block, if any — pytest's own dedicated slot for it.

    A dedicated hook rather than doing this inline at the end of
    `pytest_sessionfinish`: that hook runs interleaved with every other plugin's own
    `pytest_sessionfinish`, including the terminal reporter's, so anything printed
    there could land mid-test-output. `pytest_terminal_summary` fires once, after the
    FAILURES section and before pytest's own "short test summary info" and final
    one-line footer — the same slot pytest-cov's coverage table or
    pytest-benchmark's results print into — see `_auto_diff`.
    """
    snapshot = getattr(config, "_drift_snapshot", None)
    if snapshot is not None:
        _auto_diff(terminalreporter, snapshot.path.parent.parent, snapshot)


def _auto_diff(reporter, drift: Path, snapshot: Snapshot) -> None:
    """Print a compact diff against the nearest ancestor snapshot, if there is one.

    Every failure here — no repo, no git, `nearest_ancestor_snapshot` itself never
    raises but a malformed snapshot on disk can still break `load_snapshot`/
    `compare`, an unreadable report dir — must degrade to printing nothing and
    letting pytest finish normally. This is a report, never a gate: `exitstatus` is
    never read or touched, so a Regressed/Degraded finding here cannot change
    pytest's own exit code.

    Everything is built into `lines` before anything is printed, and report writing
    happens before the print too — so a failure partway through (a broken report
    dir, say) can never leave a half-printed, confusing block.
    """
    try:
        if not _auto_diff_enabled(reporter.config, drift):
            return
        baseline_hash = _resolve_baseline(drift, exclude=snapshot.commit_hash)
        if baseline_hash is None:
            return  # spec: no ancestor means print NOTHING, not even a notice
        before = load_snapshot(baseline_hash, drift)
        # compare()'s own removed list — not recomputed, per god's ruling.
        diffs, removed = compare(before.results, snapshot.results)
        comparability = judge_comparability(before.manifest, snapshot.manifest)
        lines = _auto_diff_lines(diffs, comparability, baseline_hash)
        if _auto_export_enabled(drift):
            created_at = _iso(datetime.now(timezone.utc).timestamp())
            _write_reports(
                diffs, comparability, before.commit_hash, snapshot.commit_hash,
                created_at, removed, drift,
            )
    except Exception:  # noqa: BLE001 - see docstring: never break the host suite
        return

    for line in lines:
        reporter.write_line(line)
