"""J7d: hand-crafted spans in, schema-valid results.json out."""

import json

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Status, StatusCode

from getdrift.adapters.otel import DriftSpanCollector, SpanConventionError
from getdrift.schema import validate_results


@pytest.fixture()
def tracer():
    """A real tracer whose finished spans feed a DriftSpanCollector."""
    collector = DriftSpanCollector(metadata={"harness": "otel-test"})
    provider = TracerProvider()
    provider.add_span_processor(collector)
    return provider.get_tracer("test"), collector


def test_hand_crafted_spans_produce_valid_results(tracer, tmp_path):
    tracer, collector = tracer

    with tracer.start_as_current_span("eval_case") as span:
        span.set_attribute("drift.case_id", "refund_policy_multi_turn")
        span.set_attribute("drift.score.answer_correctness", 0.91)
        span.set_attribute("drift.score.latency_ms", 1420)
        span.set_attribute("drift.pass", True)
        span.set_attribute("drift.metadata.turns", 3)

    # environment and pass both left to their fallbacks; ERROR status means a failure.
    with tracer.start_as_current_span("eval_case") as span:
        span.set_attribute("drift.case_id", "escalation_tone")
        span.set_attribute("drift.score.answer_correctness", 0.44)
        span.set_status(Status(StatusCode.ERROR))

    # Not an eval case: no drift.case_id, so it is skipped rather than rejected.
    with tracer.start_as_current_span("http.request") as span:
        span.set_attribute("http.method", "POST")

    written = json.loads(collector.write(tmp_path / "results.json").read_text())
    validate_results(written)  # redundant on purpose: proves the file on disk validates

    first, second = written["cases"]
    assert [c["case_id"] for c in written["cases"]] == [
        "refund_policy_multi_turn",
        "escalation_tone",
    ]
    assert first["metric_scores"] == {"answer_correctness": 0.91, "latency_ms": 1420}
    assert first["pass"] is True
    assert first["metadata"] == {"turns": 3}
    assert second["pass"] is False, "ERROR span status must fall back to a failed case"
    assert second["environment"] == "golden_set", "environment defaults to golden_set"
    assert second["timestamp"].endswith("Z")
    assert set(second["metadata"]) == {"trace_id", "span_id"}
    assert written["metadata"] == {"harness": "otel-test"}


class _Span:
    """A span stub: the collector only reads attributes, end_time and status."""

    name = "eval_case"
    end_time = 1_788_255_662_123_000_000
    status = None
    context = None

    def __init__(self, **attributes):
        self.attributes = attributes


@pytest.mark.parametrize(
    "attributes, expected",
    [
        ({"drift.case_id": "c"}, "no drift.score."),
        ({"drift.case_id": "c", "drift.score.acc": "0.9"}, "must be numeric"),
        (
            {"drift.case_id": "c", "drift.score.acc": 0.9, "drift.environment": "staging"},
            "not one of",
        ),
    ],
)
def test_broken_convention_is_loud(attributes, expected):
    from getdrift.adapters.otel import case_from_span

    with pytest.raises(SpanConventionError, match=expected):
        case_from_span(_Span(**attributes))


def test_span_end_time_becomes_an_offset_carrying_timestamp():
    from getdrift.adapters.otel import case_from_span

    case = case_from_span(_Span(**{"drift.case_id": "c", "drift.score.acc": 0.9}))
    assert case["timestamp"] == "2026-09-01T09:41:02.123Z"
    validate_results({"schema_version": "1.0.0", "cases": [case]})
