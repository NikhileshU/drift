# Drift ← promptfoo field mapping

**Status: J8a deliverable.** Verified against a real `promptfoo eval -o out.json` run,
promptfoo **0.122.2**, output `results.version: 3`.

promptfoo already produces exactly the four things Drift needs — an identifier, scores,
a pass/fail, and a time. The adapter is a pure file transform:

```
promptfoo eval -c promptfooconfig.yaml -o out.json
drift ingest promptfoo out.json -o results.json
drift snapshot --results-file results.json
```

No promptfoo config change is required for the default mapping.

---

## Where the data lives

promptfoo's JSON nests one level: `{ evalId, config, results: { version, timestamp,
prompts[], results[], stats } }`. The per-case rows are `results.results[]`. Older
promptfoo versions put the rows at the top level, so the adapter accepts either
nesting.

One row is one **(test × prompt × provider)** cell of promptfoo's matrix.

## Field map

| Drift field | promptfoo source | Notes |
|---|---|---|
| `case_id` | `testCase.description` (+ provider and/or prompt label) | see below |
| `metric_scores` | `namedScores` + `score` | `score` always included |
| `pass` | `success` (fallback `gradingResult.pass`) | |
| `environment` | — | defaults to `golden_set` |
| `timestamp` | `results.timestamp` | run-level; every case shares it |
| `metadata` | `provider.id`, `prompt.label`, `latencyMs`, `cost`, `id`, `vars`, failure reason | |

### `case_id` — the interesting one

`case_id` is what `drift diff` joins on, so it has to be stable across commits and
unique within a run. promptfoo offers three candidates and two of them are traps:

* `results[].id` is a fresh UUID on every run — it would make every case **New**, every time.
* `testIdx` is a list index — inserting a test at the top renames everything below it.

So the adapter uses **`testCase.description`**, the one human-authored stable name. A
test with no `description` falls back to `test-<12 hex of sha256(vars)>`, which is
stable as long as the test's inputs are.

A description is not unique on its own: promptfoo runs every test against every prompt
and every provider, so a 2-provider config yields two rows per description. But
**a `case_id` that already exists must never change** — renaming a case silently
destroys the snapshot history that Drift exists to preserve, so qualifying every id the
moment a second provider appears is not acceptable either.

Both constraints hold at once by giving the **unqualified id to one anchor pair** and
qualifying only the rows off it:

> The anchor is the first provider crossed with the first prompt, in
> `promptfooconfig.yaml` order. Rows on the anchor pair get the bare `description`.
> Every other row appends whichever of provider and prompt differs from the anchor.

| Run shape | Resulting `case_id`s |
|---|---|
| 1 prompt, 1 provider | `refund_policy_multi_turn` |
| + a second provider appended | `refund_policy_multi_turn` **(unchanged)**, `refund_policy_multi_turn::upper` |
| + a second prompt appended | the above, plus `refund_policy_multi_turn::prompt-b` |

So adding a provider **adds** cases and renames none. The originals keep their ids and
their whole snapshot history; the new provider's rows show up in the diff as **New**,
which is honest, because they are new. `examples/promptfoo/out.json` and
`out.two-providers.json` are two real runs of the same eval set that demonstrate exactly
this, and `tests/test_promptfoo_adapter.py` asserts it.

### Why config order is a sound anchor

promptfoo's `results.prompts[]` is its prompt x provider expansion written in config
order, and `promptIdx` on each row indexes into it — so index 0 is reliably
first-provider x first-prompt. **Appending** a provider or a prompt to
`promptfooconfig.yaml` only ever adds entries after index 0, verified against real
promptfoo 0.122.2 output.

Two details this depends on, both deliberate:

* **The anchor keys on `promptIdx`, never on a row's position.** promptfoo emits rows in
  *completion* order — with concurrency on, `results.results[0]` is simply whichever
  cell finished first, and anchoring on it would make ids non-deterministic between
  identical runs.
* **It keys on the provider's `id`, never its `label`.** Labels are frequently unset,
  and adding one later would otherwise rename every case that provider produced.

**The one case that does still re-key: reordering or prepending** a provider or prompt
in `promptfooconfig.yaml`, which moves the anchor. That is the same contract as the
description itself — the names in your config are part of your cases' identity, so
changing them changes identity. Append new providers and prompts at the end. Making
even that survive would mean reading the previous snapshot to see which pair held the
bare id, i.e. stateful ingestion coupled to Drift's storage; not worth it until someone
hits it.

Label your prompts. Without a label promptfoo uses the raw prompt text as the label, so
a qualified id would carry the whole prompt in it. (The prompt's own version belongs in
the manifest's `prompt_version`.)

### `metric_scores`

Starts from **`namedScores`** — one entry per distinct `metric:` on your assertions:

```yaml
assert:
  - type: contains
    value: "30 days"
    metric: answer_correctness   # -> metric_scores.answer_correctness
```

The row's overall weighted `score` is added as `score`, so a config with no `metric:`
labels at all is still ingestible — `namedScores` is `{}` there, and the schema requires
at least one metric.

Two things it will **not** do:

* **It never overwrites a metric you named `score` yourself.** `metric: score` is a
  plausible thing to write, and snapshots are immutable, so clobbering your value would
  be wrong forever. If the name is taken, promptfoo's overall goes to `promptfoo_score`
  and both survive.
* **It never invents a score.** If promptfoo reports no overall `score`, nothing is
  added — injecting `0.0` would be a metric the harness never produced, at the worst
  possible value, dragging the case's delta down for free. When there is genuinely
  nothing numeric to record, ingestion fails with an error naming the case rather than
  fabricating a number to satisfy the schema.

Metric names must match the schema's `^[A-Za-z0-9_.:-]+$`. A promptfoo `metric:` with a
space in it is a **loud error**, not a silently mangled name — rename it in your config.

**`latencyMs` and `cost` are deliberately NOT metrics.** They go in `metadata`. Both
swing run-to-run for reasons that have nothing to do with quality, and as metrics they
would fill the diff's Degraded bucket with noise.

### `pass`

`success` — promptfoo's row-level verdict, present even when a row has no assertions.
`gradingResult.pass` is the fallback for rows that predate the field. (Reading it as
`row["pass"]` on the Drift side, never `row.pass` — `pass` is a Python keyword.)

### `environment`

promptfoo has no equivalent concept. Defaults to `golden_set`, which is what a
promptfooconfig is: a curated, repeatable eval set. Override with
`drift ingest promptfoo … --environment production_sample` when you point promptfoo at
sampled traffic.

### `timestamp`

`results.timestamp` is run-level ISO 8601 with a `Z` offset, and every case in the run
gets it. promptfoo records no per-case time.

It is **checked once, before being copied onto every case.** One bad run-level field
would otherwise surface as one identical schema error per case — 500 regex dumps on a
500-case run, none of them naming the single field responsible. A timestamp without an
explicit offset warns and falls back to the ingestion time rather than blocking the
whole run, because the field is informational: Drift diffs on scores, not times.

### Which schema it validates against

`drift ingest promptfoo` validates against the repo's own `.drift/schema/` copy when
there is one — the same file `drift snapshot` will use — so ingest cannot pass something
snapshot then rejects. Outside a repo, or before `drift init`, it falls back to the
schema packaged with Drift, since the adapter is useful as a library in both places.

## Snapshot provenance — the three `drift snapshot` flags

`drift snapshot` takes `--model-version`, `--prompt-version` and `--judge-version`, and
each defaults to the literal `unset`. Leaving `judge_version` at `unset` is not
harmless: `drift diff` compares it between two snapshots to decide whether their scores
were produced by the same grader, and two `unset`s compare equal, so a rubric change
slips through as a false regression.

promptfoo knows all three, so the adapter derives them. They are written into
`results.json` under `metadata.provenance`, and `drift ingest promptfoo` prints the
ready-to-run snapshot command with the values already filled in:

```
$ drift ingest promptfoo out.json -o results.json
Wrote results.json — 2 case(s) from out.json.
Next:
  drift snapshot --results-file results.json \
    --model-version echo \
    --prompt-version 557aa7663dd9 \
    --judge-version promptfoo-asserts:sha256:c477103ab999
```

| Manifest field | Derived from | Shape |
|---|---|---|
| `model_version` | distinct `provider.id` across the run | `openai:gpt-4o`, or `a,b` for a multi-provider run |
| `prompt_version` | prompt label + promptfoo's own prompt hash | `support-agent@557aa7663dd9`, or the bare hash when unlabelled |
| `judge_version` | sha256 over the distinct assertion definitions | `promptfoo-asserts:sha256:c477103ab999` |

**`judge_version` is the assertion set,** because in promptfoo the assertions *are* the
grader — there is no separate rubric object. Hashing the distinct
`testCase.assert[]` entries means the value moves when and only when the grading
criteria move: editing an assertion changes it, a run where the same assertions merely
produce different scores does not. That is exactly the signal `drift diff` needs.

**`prompt_version` carries both the label and the content hash** (`label@hash`). The
label alone would stay fixed while someone edits the prompt underneath it; the hash
alone would be unreadable. promptfoo defaults an unlabelled prompt's label to its own
raw text, and in that case the value degrades to the bare hash.

## Run-level metadata

Written to the top-level `metadata` (which Drift never reads):
`harness: "promptfoo"`, `provenance` (above), `eval_id`, `description` from the
config, promptfoo's `stats`, and the source filename.

## Worked example

`examples/promptfoo/` holds a real offline run — `promptfooconfig.yaml` (the `echo`
provider, so it needs no API key), promptfoo's own `out.json`, and the
`results.json` the adapter produced from it.


## One run per case

This adapter emits one run per case — `metric_scores` and `pass`, with no `runs` array.
That is valid and is exactly the pre-1.1.0 shape, but it means a case has no standard
deviation, so the noise floor is 0 and `drift diff` buckets on the raw threshold alone.

To get a noise estimate, have the harness score each case more than once and emit the
`runs` array described in [`schema.md`](schema.md#casesruns--one-repeated-run-schema-110).
Repeating a case is what makes a real change separable from sampling variance; at one run
there is no estimate to make.
