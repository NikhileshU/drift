"""OpenTelemetry ingestion: eval spans in, a schema-valid results.json out.

The convention is documented in `docs/otel-convention.md` and summarised by the
constants below. A span is a Drift eval case iff it carries `drift.case_id`; every
other span in the trace is skipped.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from getdrift.schema import SCHEMA_VERSION, validate_results

PREFIX = "drift."
CASE_ID = PREFIX + "case_id"
PASS = PREFIX + "pass"
ENVIRONMENT = PREFIX + "environment"
TIMESTAMP = PREFIX + "timestamp"
SCORE_PREFIX = PREFIX + "score."
METADATA_PREFIX = PREFIX + "metadata."

ENVIRONMENTS = ("golden_set", "production_sample")
DEFAULT_ENVIRONMENT = "golden_set"

# Subclass the real SpanProcessor when the OTel SDK is installed so the collector can be
# handed straight to `provider.add_span_processor`. Without it the class still works on
# any object exposing `.attributes` / `.end_time` / `.status`, which is all it reads.
try:  # pragma: no cover - depends on whether the optional extra is installed
    from opentelemetry.sdk.trace import SpanProcessor as _SpanProcessor
except ImportError:  # pragma: no cover
    class _SpanProcessor:  # type: ignore[no-redef]
        pass


class SpanConventionError(ValueError):
    """A span declared itself a Drift case but broke the attribute convention."""


def _iso(end_time_ns: Optional[int]) -> str:
    """Span end time (ns since the UTC epoch) as an offset-carrying ISO 8601 string."""
    seconds = end_time_ns / 1_000_000_000 if end_time_ns else datetime.now(timezone.utc).timestamp()
    stamp = datetime.fromtimestamp(seconds, timezone.utc)
    return stamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _passed(attributes: Dict[str, Any], span: Any) -> bool:
    """`drift.pass` if set, else the span status — ERROR is a failure, anything else a pass."""
    if PASS in attributes:
        return attributes[PASS]
    status = getattr(span, "status", None)
    return str(getattr(status, "status_code", "")).rsplit(".", 1)[-1] != "ERROR"


def _trace_metadata(span: Any) -> Dict[str, str]:
    """Fall back to the span's own ids so a case can always be traced to its run."""
    context = getattr(span, "context", None) or getattr(span, "get_span_context", lambda: None)()
    if context is None:
        return {}
    return {"trace_id": f"{context.trace_id:032x}", "span_id": f"{context.span_id:016x}"}


def case_from_span(span: Any) -> Optional[Dict[str, Any]]:
    """Convert one span into a results.json case entry, or None if it is not an eval case."""
    attributes = dict(getattr(span, "attributes", None) or {})
    case_id = attributes.get(CASE_ID)
    if case_id is None:
        return None

    name = getattr(span, "name", "<unnamed span>")
    scores = {
        key[len(SCORE_PREFIX):]: value
        for key, value in attributes.items()
        if key.startswith(SCORE_PREFIX)
    }
    bad = sorted(k for k, v in scores.items() if isinstance(v, bool) or not isinstance(v, (int, float)))
    if bad:
        raise SpanConventionError(
            f"span {name!r} (case_id={case_id!r}): {SCORE_PREFIX}* must be numeric, "
            f"but {', '.join(bad)} is not. Drift does not coerce score strings."
        )
    if not scores:
        raise SpanConventionError(
            f"span {name!r} (case_id={case_id!r}): no {SCORE_PREFIX}<metric> attribute. "
            "Every Drift case needs at least one numeric score."
        )

    environment = attributes.get(ENVIRONMENT, DEFAULT_ENVIRONMENT)
    if environment not in ENVIRONMENTS:
        raise SpanConventionError(
            f"span {name!r} (case_id={case_id!r}): {ENVIRONMENT}={environment!r} is not "
            f"one of {ENVIRONMENTS}."
        )

    metadata = {
        key[len(METADATA_PREFIX):]: value
        for key, value in attributes.items()
        if key.startswith(METADATA_PREFIX)
    }
    return {
        "case_id": case_id,
        "metric_scores": scores,
        "pass": bool(_passed(attributes, span)),
        "environment": environment,
        "timestamp": attributes.get(TIMESTAMP) or _iso(getattr(span, "end_time", None)),
        "metadata": metadata or _trace_metadata(span),
    }


class DriftSpanCollector(_SpanProcessor):
    """An OTel span processor that accumulates Drift eval cases as spans end.

    Register it with `provider.add_span_processor(collector)`; it ignores every span
    without a `drift.case_id`. `results()` and `write()` produce a validated
    results.json, so a convention bug surfaces here rather than at snapshot time.
    """

    def __init__(self, metadata: Optional[Dict[str, Any]] = None) -> None:
        self.cases: List[Dict[str, Any]] = []
        self.metadata = dict(metadata or {})

    def on_end(self, span: Any) -> None:
        case = case_from_span(span)
        if case is not None:
            self.cases.append(case)

    # SpanProcessor's other hooks are no-ops: Drift only reads finished spans.
    def on_start(self, span: Any, parent_context: Any = None) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

    def results(self) -> Dict[str, Any]:
        """The collected cases as a validated results.json document."""
        document: Dict[str, Any] = {"schema_version": SCHEMA_VERSION, "cases": self.cases}
        if self.metadata:
            document["metadata"] = self.metadata
        validate_results(document, source="results.json (from OTel spans)")
        return document

    def write(self, path: Path) -> Path:
        """Validate and write results.json. Returns the path written."""
        path = Path(path)
        path.write_text(json.dumps(self.results(), indent=2) + "\n", encoding="utf-8")
        return path
