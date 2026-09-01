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

#: Schema version Drift writes. Files sharing its major version are accepted.
SCHEMA_VERSION = "1.0.0"
_MAJOR = SCHEMA_VERSION.split(".")[0]


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
    if isinstance(version, str) and version.split(".")[0] != _MAJOR:
        problems.append(
            f"schema_version: {version!r} is not compatible with this Drift build, "
            f"which speaks {_MAJOR}.x"
        )
    return problems


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
