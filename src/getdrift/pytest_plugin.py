"""Drift's pytest plugin: snapshot a pytest-based eval suite with no test-file changes.

Registered as a `pytest11` entry point, so `pip install getdrift` is the whole install.
It activates only in a repo that has run `drift init` — without `.drift/` it no-ops
silently, so having Drift on the environment never changes an unrelated suite's run.

Every test that runs becomes an eval case keyed by its pytest node id. Scores beyond
pass/fail are optional and are read from `record_property`, pytest's own builtin
fixture, so a suite can report them without importing Drift.
"""

import json
import warnings
from datetime import datetime, timezone
from typing import Any, Dict, List

from getdrift.gitutil import GitError
from getdrift.schema import SCHEMA_VERSION, SchemaValidationError
from getdrift.snapshot import (
    NotInitializedError,
    SnapshotError,
    SnapshotExistsError,
    create_snapshot,
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
