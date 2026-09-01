"""Loading and enforcing the Drift contract schemas.

The schemas are standalone JSON Schema files, never inline dicts. Two copies exist:

* the canonical copy shipped inside the installed package (`getdrift/schemas/`), and
* the working copy `drift init` writes to `.drift/schema/`, which is the file
  adapter authors read and reference.

Validation prefers the repo's `.drift/schema/` copy, so what is on disk in the repo
is genuinely the contract. If it is missing, Drift falls back to the packaged copy
and says so.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from jsonschema import Draft202012Validator, FormatChecker

from getdrift.resources import SCHEMAS_DIR

RESULTS_SCHEMA_FILENAME = "results.schema.json"
MANIFEST_SCHEMA_FILENAME = "manifest.schema.json"

#: Schema version Drift writes. Files with the same major version are accepted.
RESULTS_SCHEMA_VERSION = "1.0.0"
MANIFEST_SCHEMA_VERSION = "1.0.0"


class SchemaValidationError(ValueError):
    """Raised when a results.json / manifest.json does not satisfy the contract."""

    def __init__(self, source: str, problems: List[str]) -> None:
        self.source = source
        self.problems = problems
        super().__init__(f"{source} is not valid ({len(problems)} problem(s))")


def _schema_path(filename: str, drift_dir: Optional[Path]) -> Tuple[Path, bool]:
    """Return (path, is_packaged_fallback) for a schema file."""
    if drift_dir is not None:
        candidate = drift_dir / "schema" / filename
        if candidate.is_file():
            return candidate, False
    return SCHEMAS_DIR / filename, True


def load_schema(filename: str, drift_dir: Optional[Path] = None) -> Dict[str, Any]:
    path, _ = _schema_path(filename, drift_dir)
    return json.loads(path.read_text(encoding="utf-8"))


def _pointer(err) -> str:
    if not err.absolute_path:
        return "<root>"
    parts = []
    for part in err.absolute_path:
        parts.append(f"[{part}]" if isinstance(part, int) else f".{part}")
    return "".join(parts).lstrip(".")


def _validate(document: Any, schema: Dict[str, Any], source: str) -> List[str]:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(document), key=lambda e: list(e.absolute_path))
    return [f"{_pointer(err)}: {err.message}" for err in errors]


def _check_major(document: Any, expected: str, problems: List[str]) -> None:
    if not isinstance(document, dict):
        return
    version = document.get("schema_version")
    if isinstance(version, str) and version.count(".") == 2:
        if version.split(".")[0] != expected.split(".")[0]:
            problems.append(
                f"schema_version: {version!r} is not compatible with this Drift build, "
                f"which speaks {expected.split('.')[0]}.x"
            )


def validate_results(
    document: Any, source: str = "results.json", drift_dir: Optional[Path] = None
) -> None:
    """Raise SchemaValidationError unless `document` is a valid results.json."""
    schema = load_schema(RESULTS_SCHEMA_FILENAME, drift_dir)
    problems = _validate(document, schema, source)
    _check_major(document, RESULTS_SCHEMA_VERSION, problems)

    # case_id uniqueness is part of the contract but is not expressible in JSON Schema.
    if isinstance(document, dict) and isinstance(document.get("cases"), list):
        seen: Dict[str, int] = {}
        for index, case in enumerate(document["cases"]):
            if not isinstance(case, dict):
                continue
            case_id = case.get("case_id")
            if not isinstance(case_id, str):
                continue
            if case_id in seen:
                problems.append(
                    f"cases[{index}].case_id: duplicate case_id {case_id!r} "
                    f"(first seen at cases[{seen[case_id]}]); "
                    "case_id must be unique within a run"
                )
            else:
                seen[case_id] = index

    if problems:
        raise SchemaValidationError(source, problems)


def validate_manifest(
    document: Any, source: str = "manifest.json", drift_dir: Optional[Path] = None
) -> None:
    """Raise SchemaValidationError unless `document` is a valid manifest.json."""
    schema = load_schema(MANIFEST_SCHEMA_FILENAME, drift_dir)
    problems = _validate(document, schema, source)
    _check_major(document, MANIFEST_SCHEMA_VERSION, problems)
    if problems:
        raise SchemaValidationError(source, problems)
