# Drift release notes

## 0.1.0 — unreleased (Phases 0–4)

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
- **`drift ingest promptfoo <out.json>`** — converts a promptfoo run into a schema-valid
  `results.json` and prints the ready-to-run `drift snapshot` command with the derived
  provenance filled in. `--environment` overrides the default `golden_set`.

### The pytest plugin — a snapshot with no code in your test files

Installing `getdrift` registers a `pytest11` plugin. In a repo that has run `drift init`,
running `pytest` writes a snapshot of the eval results automatically — no conftest entry,
no decorator, no edit to your test files. It is deliberately quiet: re-running `pytest` on
an unchanged commit is the normal case and is not an error, a repo without `.drift/` is a
silent no-op, and no failure inside Drift can fail a test run that otherwise passed.

### Requiring a real judge version

`judge_version` defaults to the literal `unset`, and two `unset` values compare equal —
which would let a rubric change land as a clean false regression. `drift diff` handles the
reporting side (see Comparability below), but the snapshot still carries no provenance and
snapshots are never backfilled. Teams that want the guarantee can set
`require_judge_version: true` in `.drift/config.yaml`; `drift snapshot` then refuses rather
than writing a placeholder. It is **off by default**, so no existing invocation changes, and
it is enforced in `create_snapshot()` rather than the CLI so in-process callers cannot
bypass it.

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

Harness adapters ARE in this release. `drift ingest promptfoo <out.json>` converts a
promptfoo run into a schema-valid `results.json`, and `getdrift.adapters.otel` provides a
`DriftSpanCollector` (an OTel `SpanProcessor`) plus a pure `case_from_span()`. Both validate
at ingestion, so a convention or mapping bug surfaces there rather than at snapshot time.
`opentelemetry-sdk` is an optional extra (`pip install getdrift[otel]`), not a dependency.

The API below is what they are written against, and is equally what your own adapter should
use.

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

---

### Phase 4 — the CI gate and the trend view

Still part of the same unreleased 0.1.0. Nothing has been published; install from git.

#### `drift ci` — the same diff, plus an exit code

```bash
drift ci [--baseline <hash>] [--current <hash>] [--fail-on regression|degraded]
```

Runs exactly the comparison `drift diff` runs, on the same buckets, and turns it into an
exit status: `0` when the gate passes, `1` when it fails. It prints the whole bucketed
table **before** exiting, so a CI log shows which case broke rather than only a red X.

Both hashes are optional. `--current` defaults to `HEAD`. `--baseline` defaults to the
newest commit on your default branch that has a snapshot, which is read from a **new
`default_branch` config key** (`.drift/config.yaml`, default `main`) and is only
consulted when you omit the flag.

`--fail-on regression` (the default) fails when a case went pass → fail. `--fail-on
degraded` also fails on a score drop that clears the threshold with both runs still
passing. `--threshold` and `--noise-sigma` behave as they do for `drift diff`.

**A judge-version MISMATCH fails the gate.** This is the part worth knowing before you
wire it up. When the two snapshots record different judge versions, `drift ci` exits
non-zero even if not one case regressed:

```
The gate cannot pass: with the rubric changed, a clean diff is not evidence that
nothing broke. Re-snapshot the baseline under the new judge version, then
re-run.
```

That follows from what comparability already meant. A rubric change makes the comparison
uninterpretable in *both* directions, so a clean diff is not evidence of safety, and
passing a build on a diff Drift has already declined to interpret would be worse than
failing it. The remedy is in the message.

**An UNKNOWN comparability state warns but does not block.** When *neither* snapshot
records a judge version, the gate still runs and still decides on the buckets; it prints
a warning saying the result is unverified. Absence of evidence is not evidence of a
grader change, so it is not treated as one. Set `require_judge_version: true` in
`.drift/config.yaml` if you would rather that be impossible than merely warned about.

Wiring, for GitHub Actions and GitLab CI, is in
[`ci-integration.md`](ci-integration.md); a copyable workflow is in
[`../examples/ci/github-actions.yml`](../examples/ci/github-actions.yml). Two things
that bite: the gate needs full git history (`fetch-depth: 0`) because it walks the
default branch for a baseline, and it needs that branch to already have snapshots.

#### `drift trend` — reading a case across its whole history

```bash
drift trend <case_id>          # one case across every snapshot
drift trend --metric <name>    # one metric, averaged across the cases carrying it
```

`drift diff` compares two snapshots, which is the right unit for reviewing one change and
structurally blind to anything that is a property of a *sequence*. `drift trend` walks
every snapshot in `.drift/snapshots/`, ordered by each manifest's `created_at`, running
the same pairwise comparison between consecutive pairs — no new bucketing logic — and
flags two patterns:

**Slow drift.** A decline where no individual step was ever large enough to be called a
regression, so every diff along the way said Unchanged, correctly. Flagged only when
**all three** hold:

1. the score strictly decreases at every step (a flat step ends the run);
2. no step in the run was already reported Degraded or Regressed — if one was, pairwise
   diffing has already told you and there is nothing hidden to surface;
3. the run's **total** drop exceeds the raw threshold.

The third condition is doing real work: without it a series wobbling down by thousandths
would flag, and if the whole decline is smaller than what a single diff would have called
noise, there is no hidden regression. A run must span at least three snapshots, and a
snapshot the case is missing from ends the run rather than being bridged across.

**Flip-flopping.** Pass/fail alternating two or more times. Every adjacent pair produces a
confident Regressed or Fixed, so any one diff would send a reviewer hunting for a breaking
commit that does not exist; only the sequence shows the case has been flapping. Snapshots
the case is absent from are skipped, not counted as a change — a case that was not run did
not fail.

Neither detector is a gate: a flagged pattern does not change the exit code, and
`drift ci` does not consult it. `drift trend` exits 0 whether or not it flags something,
and 1 only on a usage error such as a `case_id` no snapshot contains. How to read the
output is in [`trend-view.md`](trend-view.md).

#### Your diff output has changed: `SUPPRESSED:` and `REMOVED:`

If you are upgrading, expect two lines to look different. The notes that report withheld
or missing cases now carry **literal text markers**:

```
SUPPRESSED: 3 case(s) moved past the threshold but stayed inside the noise
floor: mixed-n, noise-swing, small-drift-noisy
SUPPRESSED: 1 case(s) had a pass flip that did not survive the majority across
runs: flaky-pass
REMOVED: 1 case(s) present in 15dd05db03fe and gone from a4352a3136bb:
legacy_fax_number_lookup
```

There are two `SUPPRESSED:` reasons, not one — a score move the noise floor withheld, and
a pass/fail flip that did not survive the majority vote across a case's runs. They are
reported separately because they are different findings.

These lines previously relied on colour alone to stand out. Colour does not survive a
monochrome CI log, a piped `>` redirect, or a pasted excerpt — and these are precisely the
lines that must survive, because both report something *absent* from the table above them.
A withheld case and a dropped case are the two things a reader is least likely to notice
missing and most likely to need.

The words are deliberately distinct from the lowercase `warning:` used elsewhere: nothing
here is wrong, something is being withheld or has gone missing, and those deserve
different words. If you grep your CI logs for Drift output, these are stable strings to
match on.
