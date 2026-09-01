"""Creating and loading snapshots, independent of the CLI.

`drift snapshot` and `drift diff` are thin wrappers over this module. It exists as a
plain importable API because two other callers need it in-process: a pytest plugin
triggering a snapshot from `pytest_sessionfinish`, and the noise-aware diff work.
Shelling out to the `drift` binary would need it on PATH — false in exactly the
tox/CI/editable layouts a pytest plugin lives in — and would flatten these typed
exceptions into an exit code plus a parsed stderr string.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Union

from getdrift import __version__
from getdrift.gitutil import has_uncommitted_changes, head_hash
from getdrift.paths import drift_dir
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
        document, source = results, "<in-memory results>"
    else:
        source = str(results)
        document = _read_json(Path(results))
    validate_results(document, source=source, drift_dir=base)

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

    target.mkdir(parents=True)
    for name, doc in (("results.json", document), ("manifest.json", manifest)):
        (target / name).write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    return Snapshot(target, commit, document, manifest, dirty)


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
