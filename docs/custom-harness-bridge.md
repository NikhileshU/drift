# Bridging a custom harness into Drift

**Status: P9-3 deliverable.** Worked example verified against a real foreign repo
(`github.com/Aider-AI/aider`) — its own benchmark harness, not a synthetic fixture.

If your eval harness isn't promptfoo and doesn't emit OTel spans, there is no adapter
for it. You write a small script that reads your harness's own output and writes a
Drift `results.json` by hand. That script is what this doc calls "the bridge." It is
usually short — a loop over your harness's rows, filling in five fields per case — but
three of the five have a wrong answer that looks right at first, so read this before
you write one, not after it's already snapshotting the wrong thing.

There is no third built-in adapter and none is planned here. If a harness format turns
out to be common enough to be worth one, that is a separate, larger decision — this doc
is for the harness you have today, however unusual its shape.

## The target shape

Whatever your bridge does, it ends at one `results.json` matching
[`schema.md`](schema.md): an object with `cases[]`, each case carrying `case_id`,
`metric_scores`, `pass`, `environment`, `timestamp`. Read that doc for the full
contract; this one is only about how to *get* those five values out of a harness that
was never built to produce them.

## Worked example: Aider

[Aider](https://github.com/Aider-AI/aider) is an AI pair-programming CLI. Its
`benchmark/benchmark.py` runs an LLM against exercism-style coding exercises and writes
one `.aider.results.json` per exercise, in a schema of its own:

```
testdir, testcase, model, edit_format, tests_outcomes (list[bool]),
cost, duration, test_timeouts, commit_hash, num_error_outputs, num_user_asks,
num_exhausted_context_windows, num_malformed_responses, syntax_errors,
indentation_errors, lazy_comments, reasoning_effort, prompt_tokens,
completion_tokens, thinking_tokens, chat_hashes
```

This is neither promptfoo's shape nor OTel spans — it's what any harness built around
"run the tool, check pass/fail, tally cost" tends to look like. It's used below for
each field because the reasoning generalises past aider specifically.

### `case_id` — pick the stable thing, not the convenient thing

Aider's `testcase` field (the exercise's slug, e.g. `leap`, `anagram`) is already a
good `case_id`: a human-authored name, stable across runs and across git commits.

The trap is universal, not aider-specific: **never use a fresh identifier the harness
mints per run, and never use a list position.** promptfoo's adapter hit the identical
choice and the reasoning there is the canonical statement of why — see
[`_test_name` in `adapters/promptfoo.py`](../src/getdrift/adapters/promptfoo.py): a
per-run UUID makes every case read as **New** on every single snapshot, forever, and a
list index silently renames every case below an insertion. `case_id` is what
`drift diff` joins snapshots on; get it wrong and the diff is comparing the wrong
cases without telling you.

If your harness's rows have no stable human-authored name at all, promptfoo's fallback
generalises directly: hash the row's stable inputs (`hashlib.sha256(...).hexdigest()`,
truncated) rather than leaving the field empty or numbering rows.

### `pass` — derive it from what the harness actually verified

Aider retries a failing edit up to N times and stops at the first success, recording
each attempt's pass/fail as `tests_outcomes` (a list of bool). The case's overall
verdict is `pass = any(tests_outcomes)` — true if any attempt passed.

The general version: figure out what your harness's row *means* by "success" before
picking a Python expression for it. A harness that only ever reports a final outcome
needs no derivation. One that reports multiple attempts, multiple graders, or a
continuous score with an implicit cutoff needs you to state the rule once, in the
bridge script, rather than let whichever field happens to be truthy decide it.

One structural note worth knowing about even though it's optional: a list of per-attempt
outcomes like `tests_outcomes` is a close match for Drift's optional `runs[]` array
(described in [`schema.md`](schema.md#casesruns--one-repeated-run-schema-110)) — each
attempt could become one `run` entry, giving `drift diff` a real noise estimate instead
of treating the case as a single sample. The catch: `runs[].metric_scores` requires a
score *per run*, and a harness like aider's only tracks cost/tokens cumulatively per
case, not per attempt — so populating it means synthesizing per-run numbers (splitting
the total, or repeating it), not reading them off the harness directly. Worth doing if
you actually want the noise floor; skip it and stay at case-level fields otherwise —
both are valid per the schema.

### `environment` — usually a hardcoded constant, and that's fine

Aider's schema has no concept of environment at all. Exercism exercises are a curated,
repeatable benchmark set, so the bridge hardcodes `"golden_set"`.

This is the common case for a bridge, not a special one: most single-purpose harnesses
were built to run one kind of eval, so there's nothing to branch on. Hardcode the value
that's actually true of what the harness runs. If your bridge later needs to feed the
same harness sampled production traffic instead of a fixed set, that's the moment to
make it a bridge-script flag — not before.

### `timestamp` — the one field a bridge cannot recover if the harness didn't keep it

Aider's schema has no timestamp field anywhere — not per case, not per run. If your
harness is the same, the bridge has exactly one option: stamp the case with **the time
the bridge runs**, not the time the harness actually produced the row. Be honest with
yourself about what this means: if you're bridging output that's a week old, your
`results.json` will claim it was evaluated today. Drift doesn't currently read
`timestamp` for anything beyond a display field on `drift log`, so this doesn't corrupt
a diff — but it does mean the field is telling you when you *ingested* the result, not
when the model produced it, and treating those as the same fact will eventually surprise
someone reading `drift log` a year from now.

If your harness does keep a real timestamp anywhere — even a coarse run-level one
promptfoo-style, not per-case — use it. The dishonest answer here is not "the bridge
falls back to now," it's not noticing you had a real timestamp available and threw it
away.

### `metric_scores` — the one field genuinely under revision, so this doc stops short

Aider's only continuous, per-case numeric fields are engineering signals — `cost`,
`duration`, `prompt_tokens`, `completion_tokens` — not a quality score from a grader.
There is a live, unsettled question about how Drift's metric model should treat metrics
like these versus a 0–1 quality score, and that design is in progress elsewhere on this
project (not in this doc, and not decided as of this writing). If your harness is in
the same position — nothing but engineering metrics, no judge score — hold off on
picking a `metric_scores` convention until that lands, rather than build a bridge
around an answer that's likely to change under it.

## Checklist for any other harness

1. Find the one field your harness already produces that's a stable, human-meaningful
   name for the case. If there isn't one, hash the case's stable inputs — never a
   per-run id, never a list position.
2. State, in one sentence, what your harness means by "this case passed" — then write
   that sentence as the `pass` expression, rather than reaching for whichever boolean
   field is closest.
3. Hardcode `environment` to whatever's actually true of the harness's one mode. Only
   make it a flag once you actually have two modes to choose between.
4. If the harness records no timestamp, use bridge-run time and know that's what it is.
   If it records one anywhere — even run-level — use it.
5. For `metric_scores`: a 0–1 quality score from a grader is the safe, settled case.
   Anything else — cost, latency, token counts — wait for the metric-direction design
   to land before committing to a convention.
