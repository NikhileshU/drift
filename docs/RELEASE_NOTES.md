# Drift release notes

## 0.1.0 — unreleased (Phases 0–3)

First working version: immutable eval snapshots keyed by git commit, a six-bucket diff
between any two, a strict schema contract, and a diff that refuses to give you a verdict
it cannot stand behind.

### ⚠️ Upgrading a repo that has already run `drift init`

**Re-run `drift init`.** It is not optional and it is not only for new repos.

`drift init` writes `.drift/schema/results.schema.json` and
`.drift/schema/manifest.schema.json` into the repo, and validation **prefers the repo's
on-disk copy over the one shipped inside the installed package** — deliberately, so the
file sitting in your repo genuinely is the contract. The consequence is that upgrading
Drift does not upgrade the schemas your repo validates against. Until you re-run
`drift init`, a results file using anything the newer schema added is rejected by the
stale on-disk copy, and the error points at your results file rather than at the schema,
so it looks like your adapter broke when nothing is wrong with it.

`drift init` always rewrites the schema files, so re-running it is the whole fix, and it
is safe: it does not touch your snapshots.

`drift snapshot` and `drift diff` now warn when a file in `.drift/schema/` differs from
the one the installed build ships, naming `drift init` as the remedy — but the warning
arrives after you have already been confused, so do it as part of the upgrade.

### Commands

- **`drift init`** — scaffolds `.drift/` with `config.yaml`, `snapshots/`, and the two
  JSON Schemas.
- **`drift snapshot --results-file …`** — validates a results file and writes an
  immutable snapshot against `git rev-parse HEAD`. A commit that already has a snapshot
  is a hard error, and there is no `--force`: overwriting would make every past diff
  unreproducible. Takes `--model-version`, `--prompt-version` and `--judge-version`.
- **`drift diff <hash1> <hash2>`** — buckets every case into Fixed, Regressed, Improved,
  Degraded, Unchanged or New. Accepts unambiguous hash prefixes. `--threshold` overrides
  the score delta that counts as Improved/Degraded (default `0.05`, or `diff_threshold`
  in `.drift/config.yaml`).

### The schema contract

`results.json` and `manifest.json` are JSON Schema draft 2020-12, documented field by
field in [`schema.md`](schema.md). Both set `additionalProperties: false`: an adapter
that invents a key gets a loud error rather than having its data silently ignored. Every
object that could plausibly need extra data has a free-form `metadata` escape hatch.

`schema_version` is checked on both major **and** minor. A build refuses a file written
against a newer minor version rather than validating it, ignoring the fields it does not
know about, and printing confident verdicts computed from data it only partly read.
Silent wrong answers are the worst failure this tool can have. Older-or-equal minors are
still accepted, so the check only refuses what a build genuinely cannot read.

### Comparability — `drift diff` can decline to give a verdict

A score is the output of a grader. Comparing a score from one rubric against a score
from another and calling the difference a regression is a category error: the model may
not have moved at all.

`drift diff` compares the two snapshots' `judge_version` and takes one of three paths:

- **Same judge** — the six buckets, as normal.
- **Judge changed** — prints "Not directly comparable — judge version changed from X to
  Y", withholds Fixed / Regressed / Improved / Degraded **and Unchanged**, and still
  prints every case's raw before, after and delta. New and the removed-cases line
  survive, because whether a case exists does not depend on who graded it.
- **No judge version recorded** — warns that the verdicts are unverified and **shows
  them anyway**. An unrecorded judge version is an absence of evidence, not evidence of
  a change; suppressing it would blank the diff for every team that has not adopted the
  flag, on every run, and a warning that always fires is one nobody reads.

Pass `--judge-version` to `drift snapshot` to get out of the third case. Any stable
string works as long as it changes when your grader changes — a value that never changes
is worse than none, because it turns a warning into a false all-clear.

Full behaviour in [`comparability.md`](comparability.md).

`drift diff` exits 0 in all three states. Exit-code semantics belong to the CI gate and
are being designed as one contract rather than established piecemeal, so do not build a
gate on this command's exit status yet.

### For adapter and plugin authors

Harness adapters (`drift ingest`) are not in this release; they land separately. The
API below is what they are written against.

`getdrift.snapshot` is an importable API behind the CLI — `create_snapshot`,
`load_snapshot`, `resolve_snapshot`, and a `Snapshot` dataclass carrying `.results`,
`.manifest`, `.commit_hash` and `.path`. In-process callers should use it rather than
shelling out to the `drift` binary, which is not on PATH in the tox/CI/editable layouts
a pytest plugin actually runs in, and which flattens typed exceptions into an exit code
plus a parsed stderr string.

`load_snapshot` returns `manifest=None` for a snapshot directory with no `manifest.json`
rather than raising; `drift diff` treats that as an unknown judge version.

Two gotchas worth stating plainly:

- `pass` is a Python keyword. Read it as `case["pass"]`.
- `case_id` is the only key `drift diff` matches on. It must be stable across commits:
  derive it from something durable such as a file path plus a slug, or a dataset row id
  — never a list index, a timestamp, or a hash of the model output. A case_id that
  changes between snapshots is reported as one New case and one silently dropped one.

### Noise-aware diffing — results schema 1.0.0 → **1.1.0**

**This is the change that makes the `drift init` note at the top of this file
mandatory rather than advisory.** The new `runs` array is rejected by a 1.0.0 schema —
`additionalProperties: false` doing its job — so in a repo that already ran `drift init`,
every runs-bearing results file fails validation until `drift init` is re-run to refresh
`.drift/schema/`. The error names your results file, not the stale schema, so it reads
as an adapter bug. **Re-run `drift init` as part of this upgrade.**

An eval scored once is a sample, not a measurement, and Drift used to call a case
Regressed on a single noisy draw. Now a case can carry its repeated runs:

```json
{
  "case_id": "support/refund-tone",
  "metric_scores": { "accuracy": 0.81 },
  "pass": true,
  "environment": "golden_set",
  "timestamp": "2026-09-01T09:41:02Z",
  "runs": [
    { "metric_scores": { "accuracy": 0.80 }, "pass": true },
    { "metric_scores": { "accuracy": 0.85 }, "pass": true },
    { "metric_scores": { "accuracy": 0.78 }, "pass": false }
  ]
}
```

- **`runs` is optional and additive.** Nothing in 1.0.0 changed type or became required.
  A case without `runs` is one run — standard deviation 0, noise floor 0 — and therefore
  buckets exactly as it did before. Every existing snapshot diffs to the same verdicts,
  and an adapter that never emits `runs` needs no change.
- **`drift diff` requires a change to clear the noise.** The cutoff is
  `max(diff_threshold, noise_sigma × sqrt(sd_before² + sd_after²))`, sigma defaulting to
  2.0 and settable via `noise_sigma` in `.drift/config.yaml` or `--noise-sigma`. The two
  thresholds are combined with `max`, never by replacement: a single-run case has a floor
  of 0, and replacing the threshold with it would make every rounding wobble a verdict.
- **A case passes if most of its runs did**, with an exact tie falling back to the
  case-level `pass`. One flaky failure is no longer a Regressed.
- **Suppressed is not hidden.** Cases the floor withheld are listed by name with their
  real deltas, under a line saying how many moved and why. Pass flips that did not
  survive the majority get their own line.
- **`runs_per_case`** (default 3) is the count Drift *expects* — it does not run your
  evals — and `drift snapshot` warns when a case carries fewer.
- **`metric_scores` stays required** as the compatibility surface for readers that know
  nothing about `runs`. When it disagrees with the mean of the runs by more than 5×10⁻⁴,
  `drift snapshot` warns *and* records the discrepancy under the case's
  `metadata.drift.metric_scores_discrepancy` — a warning scrolls past, and an immutable
  snapshot does not.

Worked demonstration in [`../examples/noisy-golden-set/`](../examples/noisy-golden-set/README.md):
eight cases, sampled from a pinned seed, four verdicts changed by the filter and three of
those four were false. It includes one case where the filter hides a *real* drop, because
at three runs and that much variance it genuinely is not separable from noise — the
remedy there is more runs, never a smaller sigma.
