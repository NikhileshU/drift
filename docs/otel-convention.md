# Drift OTel span-attribute convention

**Status: J7a deliverable — the ingestion contract for `getdrift.adapters.otel`.**

Drift ingests eval results from OpenTelemetry by reading span attributes. This file
defines which attributes it reads and what they become in `results.json`. It is a
*mapping* onto the frozen schema in [`schema.md`](./schema.md); it adds no fields and
changes none.

The rule for instrumenting an eval is: emit one span per eval case, set the `drift.*`
attributes below on it, register `DriftSpanCollector` as a span processor. Nothing else.

---

## Which spans are ingested

**A span is a Drift eval case if — and only if — it carries a `drift.case_id`
attribute.** That is the whole subscription rule. Span name, tracer name and
parent/child position are all ignored, so Drift attaches to whatever an existing
harness already emits without asking anyone to rename their spans.

Spans without `drift.case_id` are skipped silently: an eval run's trace is mostly
HTTP, LLM and retrieval spans, and dropping them is normal, not an error.

## Attribute map

| Span attribute | OTel type | → `results.json` | Required |
|---|---|---|---|
| `drift.case_id` | string | `cases[].case_id` | yes — also the subscription trigger |
| `drift.score.<metric>` | int/float | `cases[].metric_scores["<metric>"]` | yes, ≥1 |
| `drift.pass` | bool | `cases[].pass` | no — falls back to span status |
| `drift.environment` | string | `cases[].environment` | no — defaults to `golden_set` |
| `drift.timestamp` | string | `cases[].timestamp` | no — defaults to span end time |
| `drift.metadata.<key>` | any primitive | `cases[].metadata["<key>"]` | no |

OTel attribute values may only be primitives (or homogeneous sequences of them), which
is why scores and metadata are **flat, dotted keys** rather than nested objects. That
is a constraint of OTel, not a Drift preference.

### `drift.case_id`
The join key `drift diff` matches on. Same stability rule as the schema: derive it from
the eval file path plus a slug or a dataset row id — never from a loop index, a
timestamp, or the span id.

### `drift.score.<metric>`
Everything after the `drift.score.` prefix is the metric name, passed through verbatim.
It must match the schema's `^[A-Za-z0-9_.:-]+$`, so `drift.score.answer_correctness`
and `drift.score.rouge-l` are fine. At least one score attribute is required — a case
with no scores cannot be bucketed by score delta, so the schema rejects it.

Values must be numeric. A score set as a string (`"0.91"`) is a rejected span, not a
silently coerced one: silent coercion is how a metric ends up meaning two things.

### `drift.pass`
If the attribute is absent, Drift derives it from the **span status**: `ERROR` → `false`,
anything else (`OK`, `UNSET`) → `true`. That fallback exists because a harness that
already fails a span on a failed assertion should not have to say so twice. Set the
attribute explicitly whenever pass/fail is a scoring decision rather than an exception.

### `drift.environment`
`golden_set` or `production_sample`, exactly as the schema spells them. Defaults to
`golden_set`: the overwhelmingly common case, and the safe one — mislabelling a curated
run as production sampling would be the worse error. Any other value is a rejected span.

### `drift.timestamp`
Optional override, ISO 8601 with an explicit offset. Normally omit it and let Drift use
the span's end time, converted to UTC with millisecond precision
(`2026-09-01T09:41:02.123Z`). Span timestamps are ns-since-epoch UTC integers, so this
conversion cannot produce the naive local time the schema rejects.

If you do set it, it is checked **as the span is converted**, and a value with no
explicit offset raises `SpanConventionError` naming the span. A bad `drift.timestamp` is
almost always one instrumentation bug repeated across every span, so failing once with
the span's name beats emitting the schema's regex once per case.

### `drift.metadata.<key>`
Free-form per-case metadata: prompt hash, trace id, latency, retry count. It lands in
the schema's `metadata` escape hatch and Drift never reads it. This is where
harness-specific data goes — the case object rejects unknown top-level keys on purpose.

If no `drift.metadata.*` attributes are set, Drift records the span's own
`trace_id`/`span_id` as metadata anyway, so a case in a snapshot can always be traced
back to the run that produced it.

## Rejected spans

A span that carries `drift.case_id` but breaks the convention (no scores, a
non-numeric score, an unknown `environment`) is a **loud error**, not a skip: it was
explicitly declared to be a Drift case, so dropping it would silently shrink an eval
run. The collector raises `SpanConventionError` naming the span and the problem.

Duplicate `case_id`s across spans are caught by the schema validator when the results
are written, in the same error list as schema violations.

## Worked example

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from getdrift.adapters.otel import DriftSpanCollector

collector = DriftSpanCollector()
provider = TracerProvider()
provider.add_span_processor(collector)
trace.set_tracer_provider(provider)

tracer = trace.get_tracer("my-evals")
for row in golden_set:
    with tracer.start_as_current_span("eval_case") as span:
        result = run_agent(row)
        span.set_attribute("drift.case_id", row["id"])
        span.set_attribute("drift.score.answer_correctness", result.score)
        span.set_attribute("drift.pass", result.score >= 0.8)

collector.write("results.json")   # validated against the schema before writing
```

`write()` validates before it writes, so a convention bug fails at ingestion time
rather than at `drift snapshot` time.
