"""Drift's pytest plugin: snapshot a pytest-based eval suite with no test-file changes.

Registered as a `pytest11` entry point, so `pip install getdrift` is the whole install.
It activates only in a repo that has run `drift init` — without `.drift/` it no-ops
silently, so having Drift on the environment never changes an unrelated suite's run.

Every test that runs becomes an eval case keyed by its pytest node id. Scores beyond
pass/fail are optional and are read from `record_property`, pytest's own builtin
fixture, so a suite can report them without importing Drift.
"""

import warnings
from datetime import datetime, timezone
from typing import Any, Dict, List

from getdrift.gitutil import GitError
from getdrift.schema import SCHEMA_VERSION, SchemaValidationError
from getdrift.snapshot import NotInitializedError, SnapshotError, create_snapshot

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
        key[len(METADATA_PREFIX):]: value
        for key, value in properties.items()
        if key.startswith(METADATA_PREFIX)
    }
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


class _Collector:
    def __init__(self, environment: str) -> None:
        self.environment = environment
        self.cases: List[Dict[str, Any]] = []

    def pytest_runtest_logreport(self, report) -> None:
        """Collect finished tests. A setup failure counts: a case that could not run failed."""
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
    except SnapshotError as exc:
        # SnapshotExistsError lands here: re-running pytest on an unchanged commit is
        # normal, not a failure.
        _report(config, str(exc))
        return
    except (GitError, SchemaValidationError) as exc:
        warnings.warn(f"Drift: no snapshot written — {exc}", stacklevel=1)
        return

    _report(config, f"snapshot written: {snapshot.path} ({len(collector.cases)} case(s))")


def _report(config, message: str) -> None:
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_sep("-", f"Drift: {message}")
