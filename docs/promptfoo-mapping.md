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
and every provider, so a 2-provider config yields two rows per description. The adapter
appends **only the axes that actually vary in this run**:

| Run shape | Resulting `case_id` |
|---|---|
| 1 prompt, 1 provider | `refund_policy_multi_turn` |
| 2 providers, 1 prompt | `refund_policy_multi_turn::openai:gpt-4o` |
| 2 providers, 2 prompts | `refund_policy_multi_turn::openai:gpt-4o::support-agent-v7` |

The single-axis case — by far the most common — gets short, readable ids that match the
names in `promptfooconfig.yaml`.

**The one caveat, stated plainly:** adding a second provider or prompt to an existing
config changes every `case_id`, so the first diff after that shows every case as New.
That is a one-time re-baseline, and it is the honest outcome — the run genuinely is
measuring more cells than before. To avoid it, add the second axis in the same commit
you re-baseline on, and give prompts explicit stable labels:

```yaml
prompts:
  - id: file://support_agent.txt
    label: support-agent      # stable across prompt-text edits
```

Label your prompts. Without a label promptfoo uses the raw prompt text as the label, so
editing a prompt renames the case — and prompt edits are precisely what you want to
diff across. (The prompt's own version belongs in the manifest's `prompt_version`.)

### `metric_scores`

Starts from **`namedScores`** — one entry per distinct `metric:` on your assertions:

```yaml
assert:
  - type: contains
    value: "30 days"
    metric: answer_correctness   # -> metric_scores.answer_correctness
```

The row's overall weighted `score` is **always** added as `score`. That is deliberate:
`namedScores` is `{}` for a config with no `metric:` labels, and the schema requires at
least one metric, so `score` guarantees every run is ingestible with no config change
and gives one metric that is comparable across every promptfoo repo.

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
gets it. promptfoo records no per-case time. It already satisfies the schema's
explicit-offset rule; a run with no timestamp at all falls back to ingestion time.

## Run-level metadata

Written to the top-level `metadata` (which Drift never reads):
`harness: "promptfoo"`, `eval_id`, `description` from the config, promptfoo's
`stats`, and the source filename.

## Worked example

`examples/promptfoo/` holds a real offline run — `promptfooconfig.yaml` (the `echo`
provider, so it needs no API key), promptfoo's own `out.json`, and the
`results.json` the adapter produced from it.
