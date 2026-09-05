# Drift

Track LLM and agent eval results across git commits, and fail the build when they regress.

[![tests](https://github.com/NikhileshU/drift/actions/workflows/ci.yml/badge.svg)](https://github.com/NikhileshU/drift/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

## Why

Eval scores move for reasons nobody meant: a prompt edit, a model version bump, a
retrieval tweak. Unit tests catch the code that broke; nothing catches the eval case
that quietly went from 0.88 to 0.55 three commits ago. As more changes are written and
merged faster than anyone reads them, the gap between "the suite is green" and "the
agent still works" widens.

Drift keeps an immutable record of every eval run against the commit that produced it,
and tells you exactly what changed between any two — separating real regressions from
sampling noise, and refusing to guess when the grader itself changed.

## Install

```bash
pip install git+https://github.com/NikhileshU/drift
```

Requires Python 3.9+ and `git`. Installs the `drift` command.

> Not on PyPI yet — install from git.

> [!IMPORTANT]
> **Upgrading a repo that already ran `drift init`? Re-run `drift init`.** It is not
> optional and not only for new repos. Validation prefers the schema copies in your
> repo's `.drift/schema/` over the ones inside the installed package, so upgrading
> Drift does not upgrade the schemas your repo validates against. Until you re-run it,
> a results file using anything the newer schema added is rejected by the stale on-disk
> copy — and the error points at your results file, not at the schema, so it looks like
> your adapter broke. Re-running `drift init` is the whole fix and does not touch your
> snapshots. Details in [`docs/RELEASE_NOTES.md`](docs/RELEASE_NOTES.md).

## Quickstart

Everything below runs as written against the fixtures in this repo:

```bash
git clone https://github.com/NikhileshU/drift && cd drift
pip install -e .
```

**1. Scaffold `.drift/` in the repo you want to track.**

```console
$ drift init
Initializing Drift in /private/tmp/claude/support-agent
  created  .drift
  created  .drift/schema
  created  .drift/golden_set
  created  .drift/snapshots
  created  .drift/schema/manifest.schema.json
  created  .drift/schema/results.schema.json
  created  .drift/config.yaml

Drift initialized.
Next: drift snapshot --results-file <path/to/results.json>
```

**2. Snapshot your eval results against the current commit.**

```console
$ drift snapshot --results-file examples/demo/baseline.json \
    --model-version claude-opus-5 \
    --prompt-version support-agent@v6 \
    --judge-version rubric-2026-08-14
warning: 6 case(s) carry fewer than the expected 3 runs (escalation_tone_angry, greeting_smoke_test, legacy_fax_number_lookup, ...). Drift can only separate a real change from sampling noise when a case is run more than once; with one run there is no noise estimate at all.
Snapshot written: .drift/snapshots/155a50a675389e62c42cefebee4b19230f857ce7
  commit        155a50a675389e62c42cefebee4b19230f857ce7
  cases         6
  judge_version rubric-2026-08-14
```

That warning is Drift telling you it cannot do noise analysis on single-run cases. It is
not an error — see [Noise](#noise-aware-diffing) for what changes when you run each case
more than once.

**3. Change something, re-run your evals, snapshot the new commit.**

```bash
git commit -am "tune the retrieval prompt"
drift snapshot --results-file examples/demo/candidate.json \
    --model-version claude-opus-5 \
    --prompt-version support-agent@v7 \
    --judge-version rubric-2026-08-14
```

**4. Diff the two.**

```console
$ drift diff 155a50a67538 4a20859705eb

155a50a67538 → 4a20859705eb  threshold 0.05  noise 2.0σ

judge_version  rubric-2026-08-14 → rubric-2026-08-14
model_version  claude-opus-5 → claude-opus-5
prompt_version support-agent@v6 → support-agent@v7

Regressed (1)
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━┳━━━━━━━┓
┃ case_id                               ┃    pass     ┃ before ┃ after ┃ delta ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━╇━━━━━━━┩
│ escalation_tone_angry                 │ pass → FAIL │      — │     — │     — │
│   answer_correctness: 0.880→0.550     │             │        │       │       │
│ (-0.330)  citation_precision:         │             │        │       │       │
│ 0.880→0.550 (-0.330)                  │             │        │       │       │
└───────────────────────────────────────┴─────────────┴────────┴───────┴───────┘

Degraded (1)
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━┳━━━━━━━┓
┃ case_id                               ┃    pass     ┃ before ┃ after ┃ delta ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━╇━━━━━━━┩
│ multi_hop_inventory_question          │ pass → pass │      — │     — │     — │
│   answer_correctness: 0.900→0.640     │             │        │       │       │
│ (-0.260)  citation_precision:         │             │        │       │       │
│ 0.900→0.640 (-0.260)                  │             │        │       │       │
└───────────────────────────────────────┴─────────────┴────────┴───────┴───────┘

Fixed (1)
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━┳━━━━━━━┓
┃ case_id                               ┃    pass     ┃ before ┃ after ┃ delta ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━╇━━━━━━━┩
│ refund_policy_multi_turn              │ FAIL → pass │      — │     — │     — │
│   answer_correctness: 0.420→0.810     │             │        │       │       │
│ (+0.390)  citation_precision:         │             │        │       │       │
│ 0.420→0.810 (+0.390)                  │             │        │       │       │
└───────────────────────────────────────┴─────────────┴────────┴───────┴───────┘

Improved (1)
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━┳━━━━━━━┓
┃ case_id                               ┃    pass     ┃ before ┃ after ┃ delta ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━╇━━━━━━━┩
│ sku_lookup_ambiguous                  │ pass → pass │      — │     — │     — │
│   answer_correctness: 0.610→0.790     │             │        │       │       │
│ (+0.180)  citation_precision:         │             │        │       │       │
│ 0.610→0.790 (+0.180)                  │             │        │       │       │
└───────────────────────────────────────┴─────────────┴────────┴───────┴───────┘

New (1)
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━┳━━━━━━━┳━━━━━━━┓
┃ case_id                                  ┃   pass   ┃ before ┃ after ┃ delta ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━╇━━━━━━━╇━━━━━━━┩
│ tool_call_retry_on_timeout               │ — → pass │      — │     — │     — │
│   answer_correctness: —→0.730 (—)        │          │        │       │       │
│ citation_precision: —→0.730 (—)          │          │        │       │       │
└──────────────────────────────────────────┴──────────┴────────┴───────┴───────┘

Unchanged (1)
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━┳━━━━━━━┓
┃ case_id                               ┃    pass     ┃ before ┃ after ┃ delta ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━╇━━━━━━━┩
│ greeting_smoke_test                   │ pass → pass │      — │     — │     — │
│   answer_correctness: 0.950→0.960     │             │        │       │       │
│ (+0.010)  citation_precision:         │             │        │       │       │
│ 0.950→0.960 (+0.010)                  │             │        │       │       │
└───────────────────────────────────────┴─────────────┴────────┴───────┴───────┘

Regressed 1  Degraded 1  Fixed 1  Improved 1  New 1  Unchanged 1
REMOVED: 1 case(s) present in 155a50a67538 and gone from 4a20859705eb:
legacy_fax_number_lookup
```

Every case here carries two metrics (`answer_correctness`, `citation_precision`), so the
top-level `before`/`after`/`delta` columns read `—` and the real numbers are on the
per-metric line under the `case_id` — see [Buckets](#how-it-works) for why. Your own
hashes will differ. Both accept any unambiguous prefix.

To use your own evals, point `--results-file` at whatever your harness produces once it
conforms to [the schema](docs/schema.md) — or convert it with
[`drift ingest`](#drift-ingest) or the [integrations](#integrations) below.

## Commands

### `drift init`

Creates `.drift/` in the repo root: `config.yaml`, the two JSON Schema files, and empty
`golden_set/` and `snapshots/` directories. Safe to re-run — it fills in what is missing
and leaves your config alone.

| Flag | Meaning |
|---|---|
| `--force` | Overwrite an existing `.drift/config.yaml` with the stub template. |

```bash
drift init
```

### `drift snapshot`

Validates a results file against the schema and writes it to
`.drift/snapshots/<commit_hash>/` with a manifest recording the commit, the versions you
pass, and when it was written.

| Flag | Meaning |
|---|---|
| `--results-file PATH` | **Required.** A results.json conforming to `.drift/schema/results.schema.json`. |
| `--model-version TEXT` | Model under test. Free text. Default `unset`. |
| `--prompt-version TEXT` | Prompt / agent config version. Free text. Default `unset`. |
| `--judge-version TEXT` | Scoring rubric or judge version. Default `unset`. |

```bash
drift snapshot --results-file results.json --judge-version rubric-2026-08-14
```

Pass `--judge-version`. `drift diff` compares it between snapshots to decide whether
their scores are comparable at all, and two `unset` values compare equal — so leaving it
out defeats the check silently. Set `require_judge_version: true` in `.drift/config.yaml`
to make Drift refuse the snapshot instead.

### `drift diff`

Compares two snapshots and prints the bucketed table shown in the quickstart. Takes a
full commit hash or any unambiguous prefix.

| Flag | Meaning |
|---|---|
| `--threshold FLOAT` | Score delta counted as Improved/Degraded. Defaults to `diff_threshold` in config, else `0.05`. |
| `--noise-sigma FLOAT` | Combined standard deviations a change must clear. Defaults to `noise_sigma` in config, else `2.0`. `0` disables the noise filter. |
| `--environment [golden_set\|production_sample]` | Compare only cases recorded under this environment. |

```bash
drift diff 15dd05db03fe a4352a3136bb
drift diff 15dd05d a4352a3 --threshold 0.1 --noise-sigma 0
```

A case run under different environments on each side (a `golden_set` case later sampled
in `production_sample`, say) gets no verdict at all — its score moved for a reason that
has nothing to do with the model, and a bucket would claim otherwise:

```console
$ drift diff 4e84ceb78677 0c5ded68db18

4e84ceb78677 → 0c5ded68db18  threshold 0.05  noise 2.0σ

judge_version  demo → demo
model_version  unset → unset
prompt_version unset → unset

Regressed 0  Degraded 0  Fixed 0  Improved 0  New 0  Unchanged 0
Scores only, no verdict (1)
┏━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━┳━━━━━━━━┓
┃ case_id              ┃        pass ┃ before ┃ after ┃  delta ┃
┡━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━╇━━━━━━━━┩
│ sku_lookup_ambiguous │ pass → pass │  0.610 │ 0.400 │ -0.210 │
└──────────────────────┴─────────────┴────────┴───────┴────────┘

SUPPRESSED: 1 case(s) compared across different environments, no verdict shown:
sku_lookup_ambiguous (golden_set vs production_sample). Pass --environment
<golden_set|production_sample> to compare only one.
```

`drift ci` and `drift trend` take the same `--environment` flag, for the same reason.

### `drift ci`

The same comparison as `drift diff`, plus an exit code. Prints the full table first, so
a CI log shows what actually failed rather than just a red X.

| Flag | Meaning |
|---|---|
| `--baseline TEXT` | Snapshot to compare against. Defaults to the newest commit on `default_branch` (config, default `main`) that has a snapshot. |
| `--current TEXT` | Snapshot under test. Defaults to `HEAD`. |
| `--fail-on [regression\|degraded]` | `regression` (default) fails on a pass→fail case. `degraded` also fails on a score drop that clears the threshold with both runs still passing. |
| `--threshold FLOAT` | As `drift diff`. |
| `--noise-sigma FLOAT` | As `drift diff`. |
| `--environment [golden_set\|production_sample]` | Gate on only cases from this environment, applied to both snapshots. |

Exit `0` when the gate passes, `1` when it fails:

```console
$ drift ci --baseline 15dd05db03fe
...
Regressed 1  Degraded 1  Fixed 1  Improved 1  New 1  Unchanged 1

FAIL — 1 case(s) in Regressed: escalation_tone_angry
$ echo $?
1
```

```console
$ drift ci --baseline a4352a3136bb
...
Regressed 0  Degraded 0  Fixed 0  Improved 0  New 0  Unchanged 6

PASS — nothing in Regressed.
$ echo $?
0
```

A judge-version mismatch also fails the gate. When the grader changed, the comparison
cannot be trusted in either direction, so it blocks rather than reporting verdicts it
cannot stand behind.

A ready-to-copy GitHub Actions workflow is in
[`examples/ci/github-actions.yml`](examples/ci/github-actions.yml); more on wiring it
into other CI systems in [`docs/ci-integration.md`](docs/ci-integration.md).

### `drift trend`

Charts one case across every snapshot in the repo, ordered by when each was written,
and flags two patterns a pairwise diff structurally cannot see.

| Flag | Meaning |
|---|---|
| `--metric TEXT` | Chart this metric averaged across every case carrying it, instead of a single case. |
| `--threshold FLOAT` | Delta counted as Improved/Degraded between consecutive snapshots. As `drift diff`. |
| `--noise-sigma FLOAT` | Combined standard deviations a change must clear. As `drift diff`. |
| `--environment [golden_set\|production_sample]` | Chart only cases from this environment, applied to every snapshot in the history. |

**Slow drift** — a decline where no individual step was ever large enough to be called
a regression, so every diff along the way said Unchanged:

```console
$ drift trend refund_policy_multi_turn

case refund_policy_multi_turn  5 of 5 snapshots  █▆▄▂▁

SLOW DRIFT — declined across 5 consecutive snapshots, 0.920 → 0.840 (total
−0.080), without any single step being called a regression. 4a8d839e800a →
a1a0c7aceeb2.

┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━┓
┃ commit       ┃ created_at           ┃ score ┃ pass ┃  delta ┃ vs previous ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━┩
│ 4a8d839e800a │ 2026-09-02T09:22:06Z │ 0.920 │ pass │      — │ —           │
│ edaa11f07ca2 │ 2026-09-02T09:22:06Z │ 0.900 │ pass │ -0.020 │ Unchanged   │
│ a588481f0019 │ 2026-09-02T09:22:06Z │ 0.880 │ pass │ -0.020 │ Unchanged   │
│ fd7c78de93f4 │ 2026-09-02T09:22:06Z │ 0.860 │ pass │ -0.020 │ Unchanged   │
│ a1a0c7aceeb2 │ 2026-09-02T09:22:07Z │ 0.840 │ pass │ -0.020 │ Unchanged   │
└──────────────┴──────────────────────┴───────┴──────┴────────┴─────────────┘
```

Each step there is −0.020, comfortably inside the 0.05 threshold, so `drift diff` was
right to call every one of them Unchanged. Only the whole history shows the case lost
0.080.

Flagging requires all three of: the score strictly decreases at **every** step in the
run; no step was already reported as Degraded or Regressed; and the run's **total** drop
exceeds the threshold. That last condition is what stops a series wobbling down by
thousandths from flagging — if the entire decline is smaller than what one diff would
have called noise, there is no hidden regression to surface. A snapshot the case is
missing from ends the run rather than being bridged across.

**Flip-flopping** — a case alternating between pass and fail, which is unstable rather
than changed:

```console
$ drift trend escalation_tone_angry

case escalation_tone_angry  5 of 5 snapshots  ▇▁▇▁█

FLIP-FLOPPING — pass/fail changed 4 times across this history, at edaa11f07ca2,
a588481f0019, fd7c78de93f4, a1a0c7aceeb2. This case is unstable rather than
changed.

┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━┓
┃ commit       ┃ created_at           ┃ score ┃ pass ┃  delta ┃ vs previous ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━┩
│ 4a8d839e800a │ 2026-09-02T09:22:06Z │ 0.710 │ pass │      — │ —           │
│ edaa11f07ca2 │ 2026-09-02T09:22:06Z │ 0.480 │ FAIL │ -0.230 │ Regressed   │
│ a588481f0019 │ 2026-09-02T09:22:06Z │ 0.700 │ pass │ +0.220 │ Fixed       │
│ fd7c78de93f4 │ 2026-09-02T09:22:06Z │ 0.470 │ FAIL │ -0.230 │ Regressed   │
│ a1a0c7aceeb2 │ 2026-09-02T09:22:07Z │ 0.720 │ pass │ +0.250 │ Fixed       │
└──────────────┴──────────────────────┴───────┴──────┴────────┴─────────────┘
```

Any two adjacent commits here produce a confident Regressed or Fixed. Only the history
shows that neither verdict means anything: the case has been flapping the whole time.
Snapshots the case is absent from are skipped rather than counted as a change — a case
that was not run did not fail.

**Across a metric.** `--metric` averages every case carrying that metric:

```console
$ drift trend --metric answer_correctness

metric answer_correctness  5 of 5 snapshots  █▂▆▁▆

FLIP-FLOPPING — 1 case(s) carrying this metric alternate between passing and
failing: escalation_tone_angry. Averaging hides that, so they are named
individually.

┏━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━━┓
┃ commit       ┃ created_at           ┃ score ┃  delta ┃ vs previous ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━━┩
│ 4a8d839e800a │ 2026-09-02T09:22:06Z │ 0.860 │      — │ —           │
│ edaa11f07ca2 │ 2026-09-02T09:22:06Z │ 0.780 │ -0.080 │ —           │
│ a588481f0019 │ 2026-09-02T09:22:06Z │ 0.843 │ +0.063 │ —           │
│ fd7c78de93f4 │ 2026-09-02T09:22:06Z │ 0.763 │ -0.080 │ —           │
│ a1a0c7aceeb2 │ 2026-09-02T09:22:07Z │ 0.837 │ +0.073 │ —           │
└──────────────┴──────────────────────┴───────┴────────┴─────────────┘
```

The unstable case is named rather than left inside the average, since averaging is
exactly what would hide it. More on reading these charts in
[`docs/trend-view.md`](docs/trend-view.md).

### `drift ingest`

Converts another harness's output into a schema-valid results.json.

```console
$ drift ingest promptfoo out.json -o results.json
Wrote results.json — 2 case(s) from out.json.
Next:
  drift snapshot --results-file results.json \
    --model-version echo \
    --prompt-version 557aa7663dd9 \
    --judge-version promptfoo-asserts:sha256:c477103ab999
```

| Flag | Meaning |
|---|---|
| `-o, --output PATH` | Where to write the results.json. Default `results.json`. |
| `--environment TEXT` | `golden_set` (default) or `production_sample`. |

It derives the three provenance versions from promptfoo and prints the snapshot command
with them filled in, so `judge_version` is never left at `unset`.

## How it works

**The contract.** Drift reads and writes one file shape, `results.json`: a `cases` array
where each case has a stable `case_id`, a `metric_scores` object, a boolean `pass`, an
`environment`, and an offset-aware `timestamp`. Anything that can produce that file works
with Drift. Full field reference in [`docs/schema.md`](docs/schema.md).

**Immutable, git-keyed storage.** A snapshot lives at
`.drift/snapshots/<commit_hash>/` and is never overwritten. Re-running `drift snapshot`
on a commit that already has one is a hard error, and there is deliberately no `--force`:
overwriting would make every past diff unreproducible.

**Buckets.** Every case in a diff lands in exactly one:

| Bucket | Condition |
|---|---|
| Fixed | fail → pass |
| Regressed | pass → fail |
| Improved | both pass, delta clears the threshold *and* the noise floor |
| Degraded | both pass, negative delta clears the threshold *and* the noise floor |
| Unchanged | delta clears neither |
| New | `case_id` absent from the baseline |

A case carrying more than one metric is never blended into one number — a case's real
`before`/`after`/`delta` are only shown when it has exactly one shared metric. With
several, each metric is diffed against its own noise floor and gets its own verdict, and
the case's overall bucket is the *worst* of them (Regressed beats Degraded beats
Unchanged beats Improved beats Fixed) — one metric improving cannot quiet an alarm
another metric is raising:

```console
$ drift diff 3818f6fad6b9 077aea68be77

3818f6fad6b9 → 077aea68be77  threshold 0.05  noise 2.0σ

judge_version  demo → demo
model_version  unset → unset
prompt_version unset → unset

Degraded (1)
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━┳━━━━━━━┓
┃ case_id                               ┃    pass     ┃ before ┃ after ┃ delta ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━╇━━━━━━━┩
│ multi_hop_inventory_question          │ pass → pass │      — │     — │     — │
│   answer_correctness: 0.900→0.950     │             │        │       │       │
│ (+0.050)  citation_precision:         │             │        │       │       │
│ 0.850→0.550 (-0.300)                  │             │        │       │       │
└───────────────────────────────────────┴─────────────┴────────┴───────┴───────┘

Regressed 0  Degraded 1  Fixed 0  Improved 0  New 0  Unchanged 0
```

A case present in the baseline and gone from the current snapshot is reported by name
under the summary rather than silently dropped.

### Noise-aware diffing

An LLM eval scored once is a sample, not a measurement. Put N runs per case in the
results file and Drift computes a mean and standard deviation per case, then requires a
change to clear the sampling noise before calling it Improved or Degraded:

```
noise_floor      = noise_sigma × sqrt(sd_before² + sd_after²)     # default sigma 2.0
effective cutoff = max(diff_threshold, noise_floor)
```

Cases the floor withholds are still listed with their real deltas, under a line saying
how many moved. Suppressed is not hidden. A case with no `runs` array is one run —
standard deviation 0, floor 0 — so single-run results behave exactly as they always have.

Worked demonstration, eight cases and four changed verdicts:
[`examples/noisy-golden-set/`](examples/noisy-golden-set/README.md).

### Comparability

Scores are only comparable if they came from the same grader. `drift diff` compares the
two snapshots' `judge_version`; when it changed, it prints the numbers but withholds the
verdicts, because a "regression" across a rubric edit is usually the rubric. When neither
snapshot records one it warns instead — that is an absence of evidence, not evidence of a
change. Full behaviour in [`docs/comparability.md`](docs/comparability.md).

### Metric polarity

Every metric defaults to higher-is-better: a bigger score means "better." That is wrong
for a metric where the good outcome is a *smaller* number — cost, latency, duration,
tokens, error rate — and left unset, a cost blowup buckets as Improved. Declare it in
`.drift/config.yaml`:

```yaml
metric_polarity:
  cost: lower_is_better
```

Same two snapshots, `cost` 0.010 → 0.070, before and after declaring it:

```console
$ drift diff 521a2586c91c 91b3333a478b   # metric_polarity not set
...
Improved (1)
┏━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━┳━━━━━━━━┓
┃ case_id             ┃    pass     ┃ before ┃ after ┃  delta ┃
┡━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━╇━━━━━━━━┩
│ run-length-encoding │ pass → pass │  0.010 │ 0.070 │ +0.060 │
└─────────────────────┴─────────────┴────────┴───────┴────────┘

Regressed 0  Degraded 0  Fixed 0  Improved 1  New 0  Unchanged 0
$ drift diff 521a2586c91c 91b3333a478b   # cost: lower_is_better declared
...
Degraded (1)
┏━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━┳━━━━━━━━┓
┃ case_id             ┃    pass     ┃ before ┃ after ┃  delta ┃
┡━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━╇━━━━━━━━┩
│ run-length-encoding │ pass → pass │  0.010 │ 0.070 │ +0.060 │
└─────────────────────┴─────────────┴────────┴───────┴────────┘

Regressed 0  Degraded 1  Fixed 0  Improved 0  New 0  Unchanged 0
```

The stored delta is always the real, unsigned number (`+0.060` either way) — only which
bucket it lands in flips. Only `higher_is_better` (the default) and `lower_is_better` are
valid values, and `passed` cannot be given a polarity — Fixed/Regressed already covers
it. There is no CLI override: this applies uniformly to `drift diff`, `drift ci`,
`drift trend`, and the pytest plugin's auto-diff, because polarity is a property of what
the metric name means, not a tuning knob for one invocation.

## Integrations

Drift ingests from harnesses teams already use, with as little glue as possible.

- **promptfoo** — `drift ingest promptfoo out.json`. Field mapping and the `case_id`
  stability rules: [`docs/promptfoo-mapping.md`](docs/promptfoo-mapping.md).
- **pytest** — installing Drift registers a `pytest11` plugin. In a repo that has run
  `drift init`, `pytest` snapshots the suite with no import, conftest or flag in your
  test files, and per-case scores come from `record_property` — also flag-free:
  [`examples/pytest/`](examples/pytest/README.md).
- **OpenTelemetry** — register `DriftSpanCollector` as a span processor and any span
  carrying a `drift.case_id` attribute becomes an eval case:
  [`docs/otel-convention.md`](docs/otel-convention.md).
- **Anything else** — no built-in adapter, but the shape you need to hit and the traps
  in each field: [`docs/custom-harness-bridge.md`](docs/custom-harness-bridge.md).

The pytest plugin in action, on a repo whose test files mention Drift nowhere:

```console
$ pytest -q
.                                                                            [100%]- Drift: snapshot written: /private/tmp/claude/agent-evals/.drift/snapshots/f5ae143a582bc5f15393f9c1460fd1a3f9bd2b49 (1 case(s)) -

1 passed in 0.03s
```

**Auto-diff.** Every run after the first also diffs the new snapshot against the
nearest ancestor commit that already has one — not necessarily the immediately previous
commit, it walks history — and prints a compact block in pytest's own summary section.
It only ever reports; nothing here touches pytest's exit code, `drift ci` is what a
build actually fails on:

```console
$ pytest -q
.F                                                                       [100%]- Drift: snapshot written: /private/tmp/claude/agent-evals/.drift/snapshots/c3a504bd9dd25792c8c0a5b60b8c251e7063b168 (2 case(s)) -

=================================== FAILURES ===================================
...
── Drift ───────────────────────────────
  vs e1e04728 (previous run, same branch)

  warning: neither snapshot records a judge version, so Drift cannot tell whether the grader changed between them. The verdicts below are unverified — pass --judge-version to `drift snapshot` so Drift can check them.

  Regressed 1: test_eval.py::test_refund_policy
  Degraded  0
  Fixed     0
  Improved  0
  New       0
  Unchanged 1
────────────────────────────────────────
=========================== short test summary info ============================
FAILED test_eval.py::test_refund_policy - assert False
1 failed, 1 passed in 0.08s
```

The same run also writes a JSON and Markdown report to `.drift/reports/`: an
always-overwritten `latest.<ext>` — the thing to open right now — plus a timestamped
archive that is never overwritten, one history entry per commit:

```console
$ ls .drift/reports/
2026-09-04T181418776Z_c3a504bd9dd2.json  2026-09-04T181418776Z_c3a504bd9dd2.md
latest.json                              latest.md
```

Three optional `config.yaml` keys control this:

| Key | Default | Meaning |
|---|---|---|
| `auto_diff` | `true` | Print the terminal block above. `DRIFT_AUTO_DIFF=0` turns it off for one run regardless of config. |
| `auto_export` | `true` | Write the report files. |
| `export_formats` | `[json, md]` | Which format(s) to write — a bare string or a list. |

## Comparison to alternatives

promptfoo, Braintrust and Langfuse are eval *platforms*: they run your evals, grade them
and store the results, mostly in a hosted product with a web UI.

Drift is not that, and does not replace them. It does not run evals or grade anything —
it reads a results file. The differences that follow from that:

- **Repo-embedded.** Snapshots live in `.drift/` in your repo, keyed by commit hash. No
  account, no hosted component, no data leaving your machine.
- **Harness-agnostic.** The `results.json` contract is the only integration surface, so
  promptfoo, a pytest suite, an OTel trace and an in-house runner all feed the same
  history. You can change harness without losing it.
- **Regression-shaped.** The unit of output is a bucketed diff between two commits and a
  CI exit code, not a dashboard.

Using promptfoo to run evals and Drift to track them across commits is a normal setup —
that is what `drift ingest promptfoo` is for.

## Contributing

Issues and pull requests welcome at
[github.com/NikhileshU/drift](https://github.com/NikhileshU/drift).

```bash
git clone https://github.com/NikhileshU/drift && cd drift
pip install -e ".[dev]"
python -m pytest
```

Install with the `[dev]` extra, not a bare `pip install -e .` — the OTel adapter's tests
import `opentelemetry`, and without it `tests/test_otel_adapter.py` fails during
*collection*, which reads like a broken checkout rather than a missing dependency.

The suite is 412 tests.

## License

MIT — see [LICENSE](LICENSE).
