# Drift schema reference — `results.json` and `manifest.json`

**Status: signed off. Current version 1.1.0** — 1.1.0 added the optional per-case
`runs` array; nothing in 1.0.0 changed type or became required, so 1.0.0 files remain
valid.
This is the contract every other Drift workstream is written against — the OTel /
promptfoo / pytest adapters and the noise-aware diffing both read and write these two
files. Changing it after sign-off is expensive, so read it as a contract, not a sketch.

Canonical files:

| File | Location in a repo | Written by |
|---|---|---|
| Results schema | `.drift/schema/results.schema.json` | `drift init` |
| Manifest schema | `.drift/schema/manifest.schema.json` | `drift init` |
| Snapshot results | `.drift/snapshots/<commit_hash>/results.json` | `drift snapshot` |
| Snapshot manifest | `.drift/snapshots/<commit_hash>/manifest.json` | `drift snapshot` |

Both schemas are JSON Schema **draft 2020-12**. Drift ships canonical copies inside the
installed package and `drift init` writes them into `.drift/schema/`; validation reads
the repo's `.drift/schema/` copy when it exists, so the file in the repo really is the
contract. `drift init` refreshes those files to match the installed Drift version.

---

## `results.json`

One eval run's per-case outcomes. Top level is an **object**, not a bare array — so
that run-level fields can be added later without breaking every reader.

```json
{
  "schema_version": "1.0.0",
  "cases": [ /* one entry per eval case */ ],
  "metadata": { "harness": "internal-eval-runner" }
}
```

### Top level

| Field | Type | Required | Meaning |
|---|---|---|---|
| `schema_version` | string `"MAJOR.MINOR.PATCH"` | yes | Version of this schema the file was written against. Drift accepts the same major version at an equal or older minor. A newer minor is rejected rather than partly read. |
| `cases` | array, min 1 item | yes | The per-case results. `case_id` must be unique across the array. |
| `metadata` | object | no | Free-form run-level metadata. Drift never reads it. |

Unknown top-level keys are **rejected**. Put extra data in `metadata`.

### `cases[]` — one eval case

| Field | Type | Required | Meaning |
|---|---|---|---|
| `case_id` | string, 1–512 chars | yes | Stable identifier for the eval case. |
| `metric_scores` | object, ≥1 entry, values numeric | yes | Metric name → numeric score. |
| `pass` | boolean | yes | Whether the case passed. |
| `environment` | `"golden_set"` \| `"production_sample"` | yes | Where the case was run. |
| `timestamp` | ISO 8601 string with offset | yes | When the case was evaluated. |
| `runs` | array, min 1 item | no | The individual repeated runs behind this case. |
| `metadata` | object | no | Free-form per-case metadata. Drift never reads it. |

Unknown per-case keys are **rejected** — see "Why strict" below.

### `cases[].runs[]` — one repeated run (schema 1.1.0)

Optional and additive. A case **without** `runs` is one run, whose scores are
`metric_scores` and whose verdict is `pass` — so every file written against 1.0.0 stays
valid and diffs to exactly the same verdicts.

| Field | Type | Required | Meaning |
|---|---|---|---|
| `metric_scores` | object, ≥1 entry, values numeric | yes | This run's scores. Same rules as the case-level field. |
| `pass` | boolean | yes | Whether this run passed. |
| `timestamp` | ISO 8601 string with offset | no | When this run was evaluated. |
| `metadata` | object | no | Free-form per-run metadata. Drift never reads it. |

`case_id` and `environment` are deliberately absent: they belong to the case, and
repeating them per run only invites them to disagree.

**What Drift computes from them.** When `runs` is present, `drift diff` takes the case's
mean and standard deviation from the runs and ignores `metric_scores` for bucketing, and
takes the case's verdict from the **majority** of the runs' `pass` values (an exact tie
falls back to the case-level `pass`). A score change must then clear a noise floor of
`noise_sigma × sqrt(sd_before² + sd_after²)` — combined with the raw threshold as
`max(threshold, floor)` — before it is called Improved or Degraded.

**`metric_scores` is still required, and still means something.** It is the
compatibility surface: any reader that knows nothing about `runs` uses it. Drift does
not require it to equal the mean of the runs, but it checks: when the two disagree by
more than 5×10⁻⁴, `drift snapshot` warns and records the discrepancy under the case's
`metadata.drift.metric_scores_discrepancy`. It is written into the snapshot rather than
only printed because snapshots are immutable and outlive the terminal a warning scrolled
past in. The tolerance is loose enough that rounding a summary score to three decimals —
which is ordinary — is not a discrepancy.

**`case_id`.** This is the join key. `drift diff` matches cases across two snapshots by
`case_id` and nothing else. It must be stable across commits: if a case's id changes
between two snapshots, the diff reports one **New** case and silently loses the old one.
Derive it from something durable (the eval file's path plus a slug, a dataset row id) —
never from a list index, a timestamp, or a hash of the model output.

**`metric_scores`.** Metric name → number, at least one metric. Names must match
`^[A-Za-z0-9_.:-]+$`. Scores are plain numbers with no imposed range: Drift does not
assume 0–1, so accuracy fractions, latencies and token counts can all live here. Keep
metric names stable across runs — when computing a case's score delta, `drift diff`
only considers metrics present in **both** snapshots.

**`pass`.** Drives the Fixed and Regressed buckets, which are the two that matter most
in a review. It is deliberately a separate field from the scores rather than derived
from a threshold, because the pass criterion belongs to the harness, not to Drift.
*Adapter authors:* `pass` is a Python keyword — read it as `case["pass"]`.

**`environment`.** Exactly two values. `golden_set` is a curated, repeatable eval set;
`production_sample` is sampled live traffic. Phases 0–3 only require the field to exist
and be recorded faithfully — Drift does not yet treat the two differently at diff time.

**`timestamp`.** ISO 8601 with an **explicit UTC offset** — `2026-09-01T09:41:02Z` or
`2026-09-01T05:41:07-04:00`. Naive local times are rejected: eval runs move between
laptops and CI machines, and a bare local time cannot be ordered across them.

---

## `manifest.json`

Provenance for one snapshot — which commit, which model, which prompt, which judge.
Exactly one per snapshot directory, written by `drift snapshot`.

```json
{
  "schema_version": "1.0.0",
  "commit_hash": "4f2a1c9e7b83d05a6f1e2c4b8d90a7e3f5c61b28",
  "created_at": "2026-09-01T09:41:10Z",
  "model_version": "claude-opus-5",
  "prompt_version": "support-agent@v7",
  "judge_version": "rubric-2026-08-14/sha256:3ab91f"
}
```

| Field | Type | Required | Meaning |
|---|---|---|---|
| `schema_version` | string `"MAJOR.MINOR.PATCH"` | yes | Version of this manifest schema. Drift accepts any `1.x`. |
| `commit_hash` | 40 lowercase hex chars | yes | The commit the snapshot belongs to, from `git rev-parse HEAD`. |
| `created_at` | ISO 8601 string with offset | yes | When the snapshot was written. |
| `model_version` | string, free text | yes | Whatever the harness reports for the model under test. |
| `prompt_version` | string, free text | yes | Version or hash of the prompt / agent config under test. |
| `judge_version` | string, free text | yes | Version or hash of the scoring rubric or judge. |
| `drift_version` | string | no | Version of `getdrift` that wrote the snapshot. |
| `case_count` | integer ≥ 0 | no | Number of entries in the sibling `results.json`. |
| `metadata` | object | no | Free-form snapshot metadata (CI URL, branch, dataset revision). |

**`commit_hash`.** Also the snapshot directory name, which is what makes snapshots
immutable: one commit, one snapshot, never overwritten.

**`created_at` vs. per-case `timestamp`.** They are not the same. Results may be
produced long before they are snapshotted; `created_at` is when Drift wrote the
directory.

**`judge_version` — required, even as a placeholder.** If your harness has no notion of
a judge version yet, write a literal placeholder such as `"unset"` rather than omitting
the field. It is required because the field's *existence* is a downstream contract:
`drift diff` compares `judge_version` between the two snapshots, and when it differs,
the two runs were graded by different graders, so Fixed/Regressed claims are not
directly comparable and the diff must say so instead of reporting a false regression.
A snapshot with no judge version at all cannot make that check.

---

## Why the schemas are strict

Both schemas set `additionalProperties: false` on the objects that carry contract
fields. That is deliberate: an adapter that invents a top-level key gets a loud error
at `drift snapshot` time rather than having its data silently ignored, and extending
the contract stays a reviewed change. Every schema object that could plausibly need
extra data has a free-form `metadata` object for it.

Uniqueness of `case_id` is part of the contract but cannot be expressed in JSON Schema.
Drift enforces it in the validator and reports it in the same error list as schema
violations.

## Worked examples

* `examples/results.json` — valid, three cases, both `environment` values, offset and
  `Z` timestamps, optional metadata.
* `examples/manifest.json` — valid, all required fields plus the optional ones.
* `examples/noisy-golden-set/baseline.json` and `candidate.json` — valid 1.1.0, eight
  cases carrying `runs`, including two single-run cases with no `runs` array at all so
  the mixed shape is exercised. Generated from a pinned seed by `generate.py` in the
  same directory.
* `examples/results.invalid.json` — deliberately broken; validating it produces five
  errors (bad enum, non-numeric score, non-boolean `pass`, non-ISO timestamp, duplicate
  `case_id`) and is the fixture for the rejection-path test.

## Open decisions taken while writing this (flag at sign-off)

1. **Object wrapper, not a bare array.** `results.json` is `{schema_version, cases[]}`.
   The spec listed only per-case fields; a wrapper leaves room for run-level fields
   (notably the noise-aware `runs` shape) without a breaking change.
2. **`schema_version` is required in both files.** Not in the spec's field list. It is
   what makes a later schema change detectable instead of silently misread.
3. **`metric_scores` requires at least one metric.** A case with no scores cannot be
   bucketed by score delta.
4. **Timestamps require an explicit UTC offset.**
5. **Unknown properties rejected, with a `metadata` escape hatch.**
6. **`case_id` uniqueness enforced in code**, since JSON Schema cannot express it.
7. **Optional `drift_version` and `case_count`** added to the manifest as cheap
   provenance and an integrity check. Neither is required.
