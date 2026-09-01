"""Loading and enforcing the Drift contract schemas.

The schemas are standalone JSON Schema files, never inline dicts. Two copies exist:
the canonical one shipped inside the installed package, and the one `drift init`
writes to `.drift/schema/`. Validation prefers the repo's copy, so the file sitting
in the repo genuinely is the contract.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from jsonschema import Draft202012Validator, FormatChecker

SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"
RESULTS_SCHEMA_FILENAME = "results.schema.json"
MANIFEST_SCHEMA_FILENAME = "manifest.schema.json"

#: Schema version Drift writes. A file is accepted when it shares this major version
#: and its minor version is no newer — see `_version_problem`.
SCHEMA_VERSION = "1.0.0"
_MAJOR, _MINOR = (int(part) for part in SCHEMA_VERSION.split(".")[:2])

#: What `drift snapshot` writes for a provenance field left unflagged. It lives here
#: rather than beside the writer because it is a contract value with two readers:
#: `drift diff` has to recognise it too, since two snapshots both carrying it were
#: graded by unknown — possibly different — judges, and treating that as equal is
#: exactly the false verdict Drift exists to prevent.
PLACEHOLDER = "unset"


class SchemaValidationError(ValueError):
    """Raised when a results.json / manifest.json does not satisfy the contract."""

    def __init__(self, source: str, problems: List[str], schema: str = "") -> None:
        self.source = source
        self.problems = problems
        self.schema = schema  # filename of the schema that rejected it
        super().__init__(f"{source} is not valid ({len(problems)} problem(s))")


def load_schema(filename: str, drift_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Read a schema, preferring the repo's `.drift/schema/` copy over the packaged one."""
    path = drift_dir / "schema" / filename if drift_dir else None
    if path is None or not path.is_file():
        path = SCHEMAS_DIR / filename
    return json.loads(path.read_text(encoding="utf-8"))


def _problems(document: Any, filename: str, drift_dir: Optional[Path]) -> List[str]:
    validator = Draft202012Validator(
        load_schema(filename, drift_dir), format_checker=FormatChecker()
    )
    problems = [
        f"{err.json_path}: {err.message}"
        for err in sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
    ]
    version = document.get("schema_version") if isinstance(document, dict) else None
    problem = _version_problem(version)
    if problem:
        problems.append(problem)
    return problems


def _version_problem(version: Any) -> Optional[str]:
    """Why this file's declared schema_version is unusable, or None if it is fine.

    A newer MINOR is rejected as well as a newer major. Minor versions are additive,
    so an older Drift would validate the file happily against the repo's newer on-disk
    schema and then silently ignore the fields it has never heard of — reporting
    confident verdicts computed from the wrong data. Refusing is the only safe answer.
    """
    if not isinstance(version, str):
        return None  # the schema itself already rejects a non-string
    try:
        major, minor = (int(part) for part in version.split(".")[:2])
    except ValueError:
        return None  # the schema's pattern already rejects a malformed version
    if major != _MAJOR:
        return (
            f"schema_version: {version!r} is not compatible with this Drift build, "
            f"which speaks {_MAJOR}.x"
        )
    if minor > _MINOR:
        return (
            f"schema_version: {version!r} was written by a newer Drift; this build "
            f"speaks {SCHEMA_VERSION} and would silently ignore whatever {version} "
            "added. Upgrade Drift (pip install -U getdrift)."
        )
    return None


def stale_repo_schemas(drift_dir: Optional[Path]) -> List[str]:
    """Schema files in `.drift/schema/` that differ from the ones this build ships.

    They should be identical: `drift init` always rewrites them. A difference means
    the repo was initialised by a different Drift version or the files were hand-edited,
    and validation reads the repo's copy — so it is worth saying out loud.
    """
    if drift_dir is None:
        return []
    stale = []
    for packaged in sorted(SCHEMAS_DIR.glob("*.schema.json")):
        on_disk = drift_dir / "schema" / packaged.name
        if on_disk.is_file() and on_disk.read_bytes() != packaged.read_bytes():
            stale.append(packaged.name)
    return stale


def _duplicate_case_ids(document: Any) -> List[str]:
    """case_id uniqueness is part of the contract; JSON Schema cannot express it."""
    cases = document.get("cases") if isinstance(document, dict) else None
    if not isinstance(cases, list):
        return []
    seen: Dict[str, int] = {}
    problems = []
    for index, case in enumerate(cases):
        case_id = case.get("case_id") if isinstance(case, dict) else None
        if not isinstance(case_id, str):
            continue
        if case_id in seen:
            problems.append(
                f"$.cases[{index}].case_id: duplicate case_id {case_id!r} "
                f"(first seen at cases[{seen[case_id]}]); "
                "case_id must be unique within a run"
            )
        seen.setdefault(case_id, index)
    return problems


def validate_results(
    document: Any, source: str = "results.json", drift_dir: Optional[Path] = None
) -> None:
    """Raise SchemaValidationError unless `document` is a valid results.json."""
    problems = _problems(document, RESULTS_SCHEMA_FILENAME, drift_dir)
    problems += _duplicate_case_ids(document)
    if problems:
        raise SchemaValidationError(source, problems, RESULTS_SCHEMA_FILENAME)


def validate_manifest(
    document: Any, source: str = "manifest.json", drift_dir: Optional[Path] = None
) -> None:
    """Raise SchemaValidationError unless `document` is a valid manifest.json."""
    problems = _problems(document, MANIFEST_SCHEMA_FILENAME, drift_dir)
    if problems:
        raise SchemaValidationError(source, problems, MANIFEST_SCHEMA_FILENAME)
