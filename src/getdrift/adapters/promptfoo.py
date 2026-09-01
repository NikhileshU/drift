"""promptfoo ingestion: `promptfoo eval -o out.json` in, a schema-valid results.json out.

The field mapping and the reasoning behind case_id are in `docs/promptfoo-mapping.md`.
Verified against promptfoo 0.122.2 (`results.version: 3`).
"""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from getdrift.schema import SCHEMA_VERSION, validate_results

DEFAULT_ENVIRONMENT = "golden_set"
#: The schema's permitted metric-name characters.
METRIC_NAME = re.compile(r"^[A-Za-z0-9_.:-]+$")


class PromptfooFormatError(ValueError):
    """The promptfoo output could not be mapped onto the Drift schema."""


def _rows(document: Any) -> List[Dict[str, Any]]:
    """The per-case rows, from either the current nesting or the older flat one."""
    node = document
    for _ in range(2):  # {results: {results: [...]}} in 0.1xx, {results: [...]} before
        if isinstance(node, dict):
            node = node.get("results", node)
    if not isinstance(node, list):
        raise PromptfooFormatError(
            "no `results` array found. Expected the JSON that "
            "`promptfoo eval -o out.json` writes."
        )
    return [row for row in node if isinstance(row, dict)]


def _run_timestamp(document: Any) -> str:
    stamp = document.get("results", {}).get("timestamp") if isinstance(document, dict) else None
    if isinstance(stamp, str) and stamp:
        return stamp
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _test_name(row: Dict[str, Any]) -> str:
    """The stable half of case_id: the test's description, or a digest of its vars.

    Never the row's `id` (a fresh UUID per run) and never `testIdx` (a list index) —
    both would rename cases between commits and break the diff's join.
    """
    description = (row.get("testCase") or {}).get("description")
    if isinstance(description, str) and description.strip():
        return description.strip()
    payload = json.dumps(row.get("vars") or {}, sort_keys=True)
    return "test-" + hashlib.sha256(payload.encode()).hexdigest()[:12]


def _provider(row: Dict[str, Any]) -> str:
    provider = row.get("provider")
    if isinstance(provider, dict):
        return str(provider.get("label") or provider.get("id") or "")
    return str(provider or "")


def _prompt_label(row: Dict[str, Any]) -> str:
    prompt = row.get("prompt")
    return str(prompt.get("label") or prompt.get("id") or "") if isinstance(prompt, dict) else ""


def _scores(row: Dict[str, Any], case_id: str) -> Dict[str, float]:
    """namedScores (from `metric:` on assertions) plus promptfoo's overall `score`.

    `score` is always present so a config with no `metric:` labels is still ingestible —
    the schema requires at least one metric. latencyMs and cost stay out on purpose:
    they swing for reasons unrelated to quality and would flood the Degraded bucket.
    """
    scores = {
        name: value
        for name, value in (row.get("namedScores") or {}).items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    bad = sorted(name for name in scores if not METRIC_NAME.match(name))
    if bad:
        raise PromptfooFormatError(
            f"case {case_id!r}: promptfoo metric name(s) {', '.join(repr(b) for b in bad)} "
            f"contain characters the Drift schema forbids ({METRIC_NAME.pattern}). "
            "Rename the `metric:` in your promptfooconfig."
        )
    overall = row.get("score")
    scores["score"] = overall if isinstance(overall, (int, float)) and not isinstance(overall, bool) else 0.0
    return scores


def _passed(row: Dict[str, Any]) -> bool:
    """`success` is the row-level verdict; gradingResult.pass covers older output."""
    if isinstance(row.get("success"), bool):
        return row["success"]
    return bool((row.get("gradingResult") or {}).get("pass"))


def _case_metadata(row: Dict[str, Any]) -> Dict[str, Any]:
    reason = row.get("error") or (row.get("gradingResult") or {}).get("reason")
    metadata = {
        "provider": _provider(row),
        "prompt_label": _prompt_label(row),
        "latency_ms": row.get("latencyMs"),
        "cost": row.get("cost"),
        "promptfoo_result_id": row.get("id"),
        "vars": row.get("vars"),
        "reason": reason,
    }
    return {key: value for key, value in metadata.items() if value not in (None, "", {})}


def convert(
    document: Any,
    environment: str = DEFAULT_ENVIRONMENT,
    source: Optional[str] = None,
) -> Dict[str, Any]:
    """Convert parsed promptfoo output into a validated results.json document."""
    rows = _rows(document)
    if not rows:
        raise PromptfooFormatError("the promptfoo output contains no result rows.")

    # Only disambiguate case_ids by the axes that actually vary in this run, so the
    # common single-prompt/single-provider config keeps the readable names from
    # promptfooconfig.yaml. See docs/promptfoo-mapping.md for the re-baseline caveat.
    axes = [
        field
        for field in (_provider, _prompt_label)
        if len({field(row) for row in rows}) > 1
    ]
    timestamp = _run_timestamp(document)

    cases = []
    for row in rows:
        case_id = "::".join([_test_name(row)] + [field(row) for field in axes])
        cases.append(
            {
                "case_id": case_id,
                "metric_scores": _scores(row, case_id),
                "pass": _passed(row),
                "environment": environment,
                "timestamp": timestamp,
                "metadata": _case_metadata(row),
            }
        )

    config = document.get("config") or {} if isinstance(document, dict) else {}
    metadata = {
        "harness": "promptfoo",
        "eval_id": document.get("evalId") if isinstance(document, dict) else None,
        "description": config.get("description"),
        "stats": (document.get("results") or {}).get("stats") if isinstance(document, dict) else None,
        "source": source,
    }
    results = {
        "schema_version": SCHEMA_VERSION,
        "cases": cases,
        "metadata": {k: v for k, v in metadata.items() if v is not None},
    }
    validate_results(results, source=f"results.json (from promptfoo {source or 'output'})")
    return results


def convert_file(
    path: Path, environment: str = DEFAULT_ENVIRONMENT
) -> Dict[str, Any]:
    """Read `promptfoo eval -o <path>` output and convert it."""
    path = Path(path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PromptfooFormatError(f"{path} is not valid JSON: {exc}") from exc
    return convert(document, environment=environment, source=path.name)
