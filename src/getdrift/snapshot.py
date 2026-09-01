"""Creating and loading snapshots, independent of the CLI.

`drift snapshot` and `drift diff` are thin wrappers over this module. It exists as a
plain importable API because two other callers need it in-process: a pytest plugin
triggering a snapshot from `pytest_sessionfinish`, and the noise-aware diff work.
Shelling out to the `drift` binary would need it on PATH — false in exactly the
tox/CI/editable layouts a pytest plugin lives in — and would flatten these typed
exceptions into an exit code plus a parsed stderr string.
"""

import copy
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from getdrift import __version__
from getdrift.gitutil import has_uncommitted_changes, head_hash
from getdrift.paths import drift_dir, read_config
from getdrift.schema import (
    PLACEHOLDER,
    SCHEMA_VERSION,
    validate_manifest,
    validate_results,
)


class SnapshotError(RuntimeError):
    """Base for snapshot failures. Git and schema problems raise their own types."""


class NotInitializedError(SnapshotError):
    """`.drift/` does not exist — `drift init` has not been run in this repo."""


class ResultsFileError(SnapshotError):
    """The results input is missing, unreadable, or not JSON."""


class SnapshotNotFoundError(SnapshotError):
    """No snapshot matches the given hash, or a prefix matches more than one."""


class SnapshotExistsError(SnapshotError):
    """A snapshot for this commit already exists. Snapshots are never overwritten."""

    def __init__(self, commit_hash: str, path: Path, shown: Path) -> None:
        self.commit_hash = commit_hash
        self.path = path
        self.shown = shown  # path relative to the repo root, for display
        super().__init__(f"a snapshot for {commit_hash} already exists at {shown}")


@dataclass
class Snapshot:
    """One snapshot on disk."""

    path: Path
    commit_hash: str
    results: Dict[str, Any]
    #: None only if manifest.json is absent, which means a partially written snapshot.
    manifest: Optional[Dict[str, Any]] = None
    #: Whether the working tree had uncommitted changes when this was written.
    dirty: bool = False
    #: Non-fatal things worth telling the caller: too few runs, scores that disagree
    #: with their own runs. The snapshot is written either way.
    warnings: List[str] = field(default_factory=list)


def _now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ResultsFileError(f"{path} does not exist") from exc
    except json.JSONDecodeError as exc:
        raise ResultsFileError(f"{path} is not valid JSON: {exc}") from exc


#: Expected repeated runs per case when `.drift/config.yaml` does not say.
DEFAULT_RUNS_PER_CASE = 3

#: Tolerance for `metric_scores` disagreeing with the mean of `runs`. Loose enough to
#: absorb a harness rounding its summary scores to three decimals, which is ordinary and
#: not worth a warning. This check is for real disagreement — a different aggregation, a
#: stale summary, a hand-edited file — not for how a number was printed.
_TOLERANCE = 5e-4


def _reconcile_runs(document: Dict[str, Any], expected: int) -> List[str]:
    """Check each case's runs against its summary scores, annotating any disagreement.

    `drift diff` recomputes a case's mean from `runs` when they are present, so a
    `metric_scores` that disagrees with them is never what a verdict is drawn from —
    but it is what any reader who does not know about `runs` will use. A stderr warning
    is not enough on its own: snapshots are immutable and outlive the terminal the
    warning scrolled past in, so the discrepancy is recorded in the case's own metadata
    where it can still be found later.
    """
    warnings, thin, mismatched = [], [], []
    for case in document.get("cases", []):
        runs = case.get("runs")
        if not runs:
            thin.append(case["case_id"])
            continue
        if len(runs) < expected:
            thin.append(case["case_id"])
        discrepancies = {}
        for metric, reported in case["metric_scores"].items():
            values = [r["metric_scores"][metric] for r in runs if metric in r["metric_scores"]]
            if not values:
                continue
            actual = sum(values) / len(values)
            if abs(actual - reported) > _TOLERANCE:
                discrepancies[metric] = {"reported": reported, "runs_mean": actual}
        if discrepancies:
            mismatched.append(case["case_id"])
            # Namespaced so it cannot collide with whatever the harness puts here.
            case.setdefault("metadata", {}).setdefault("drift", {})[
                "metric_scores_discrepancy"
            ] = discrepancies
    if thin:
        warnings.append(
            f"{len(thin)} case(s) carry fewer than the expected {expected} runs "
            f"({', '.join(sorted(thin)[:3])}{', ...' if len(thin) > 3 else ''}). "
            "Drift can only separate a real change from sampling noise when a case is "
            "run more than once; with one run there is no noise estimate at all."
        )
    if mismatched:
        warnings.append(
            f"{len(mismatched)} case(s) have metric_scores that disagree with the mean "
            f"of their own runs ({', '.join(sorted(mismatched)[:3])}"
            f"{', ...' if len(mismatched) > 3 else ''}). Drift diffs the runs, and has "
            "recorded the discrepancy in each case's metadata.drift."
        )
    return warnings


def create_snapshot(
    results: Union[Path, str, Dict[str, Any]],
    *,
    model_version: str = PLACEHOLDER,
    prompt_version: str = PLACEHOLDER,
    judge_version: str = PLACEHOLDER,
    drift: Optional[Path] = None,
) -> Snapshot:
    """Write an immutable snapshot of `results` against the current commit.

    `results` is a path to a results.json or an already-parsed dict, so an in-process
    caller need not round-trip through a temporary file.

    Raises GitError (not a git repo / no commits), NotInitializedError, ResultsFileError,
    SchemaValidationError, or SnapshotExistsError. Nothing is written unless every
    check passes.
    """
    base = drift if drift is not None else drift_dir()
    commit = head_hash()
    dirty = has_uncommitted_changes()

    if not base.is_dir():
        raise NotInitializedError(
            "no .drift/ directory in this repo — run `drift init` first"
        )

    if isinstance(results, dict):
        # Copied because the reconciliation below annotates it; a caller's dict is
        # theirs, not ours to write into.
        document, source = copy.deepcopy(results), "<in-memory results>"
    else:
        source = str(results)
        document = _read_json(Path(results))
    validate_results(document, source=source, drift_dir=base)

    expected = read_config(base).get("runs_per_case")
    if not isinstance(expected, int) or isinstance(expected, bool) or expected < 1:
        expected = DEFAULT_RUNS_PER_CASE
    warnings = _reconcile_runs(document, expected)

    # Immutability: one commit, one snapshot, never rewritten. There is deliberately
    # no force option — overwriting would make every past diff unreproducible.
    target = base / "snapshots" / commit
    if target.exists():
        raise SnapshotExistsError(commit, target, target.relative_to(base.parent))

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "commit_hash": commit,
        "created_at": _now(),
        "model_version": model_version,
        "prompt_version": prompt_version,
        "judge_version": judge_version,
        "drift_version": __version__,
        "case_count": len(document["cases"]),
    }
    # Validated before anything is written, so a bad manifest cannot leave a
    # half-built snapshot directory that then blocks the retry.
    validate_manifest(manifest, source="generated manifest.json", drift_dir=base)

    # Serialise BEFORE mkdir. `metadata` is free-form, so a value can satisfy schema
    # validation and still be unserialisable — an in-process caller can hand us any
    # Python object. Failing after mkdir leaves an empty directory that the
    # immutability guard then treats as a real snapshot, locking that commit out
    # permanently, and the guard's own message tells the user not to delete it.
    try:
        payload = [
            (name, json.dumps(doc, indent=2) + "\n")
            for name, doc in (("results.json", document), ("manifest.json", manifest))
        ]
    except (TypeError, ValueError) as exc:
        raise ResultsFileError(
            f"results could not be serialised to JSON: {exc}. Every value in a "
            "`metadata` object must be JSON-native (str, number, bool, null, list, dict)."
        ) from exc

    target.mkdir(parents=True)
    try:
        for name, text in payload:
            (target / name).write_text(text, encoding="utf-8")
    except OSError:
        # Same reasoning: a half-written directory must never outlive a failed write.
        shutil.rmtree(target, ignore_errors=True)
        raise
    return Snapshot(target, commit, document, manifest, dirty, warnings)


def resolve_snapshot(ref: str, drift: Optional[Path] = None) -> Path:
    """Snapshot directory for a full commit hash or an unambiguous prefix of one."""
    base = drift if drift is not None else drift_dir()
    snapshots = base / "snapshots"
    if not snapshots.is_dir():
        raise NotInitializedError(
            "no .drift/snapshots/ in this repo — run `drift init` first"
        )
    exact = snapshots / ref
    if exact.is_dir():
        return exact
    matches = sorted(p for p in snapshots.glob(f"{ref}*") if p.is_dir())
    if not matches:
        raise SnapshotNotFoundError(
            f"no snapshot for {ref!r}. `ls .drift/snapshots` to see what exists."
        )
    if len(matches) > 1:
        raise SnapshotNotFoundError(
            f"{ref!r} matches {len(matches)} snapshots: "
            + ", ".join(p.name for p in matches)
        )
    return matches[0]


def load_snapshot(ref: str, drift: Optional[Path] = None) -> Snapshot:
    """Load a snapshot's results.json and manifest.json by hash or unambiguous prefix."""
    path = resolve_snapshot(ref, drift)
    results_path = path / "results.json"
    if not results_path.exists():
        raise ResultsFileError(
            f"{results_path} is missing — that snapshot directory is incomplete."
        )
    manifest_path = path / "manifest.json"
    manifest = _read_json(manifest_path) if manifest_path.exists() else None
    return Snapshot(path, path.name, _read_json(results_path), manifest)
