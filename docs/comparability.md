# Judge-version comparability — when `drift diff` refuses to give a verdict

**The short version.** If the two snapshots you are diffing were graded by different
judges, `drift diff` shows you the numbers and withholds the verdict. It is not hiding
data. It is declining to tell you that a case regressed when what actually changed is
the rubric.

Every `metric_scores` value in a snapshot is the output of some grader — an LLM judge, a
rubric, a set of assertions. Comparing a score from grader A against a score from
grader B and calling the difference a regression is a category error: the model may not
have moved at all. Drift catches this by comparing the `judge_version` field of the two
snapshots' manifests, which is why that field is required even when it only holds a
placeholder (see [`schema.md`](schema.md)).

## The three states

`drift diff` classifies every comparison as one of three, and prints the provenance
header — `judge_version`, `model_version`, `prompt_version`, before → after — on every
run so you can see which one you are in.

### EQUAL — both snapshots record the same judge version

Nothing changes. You get the six buckets exactly as always.

### MISMATCH — both record a judge version, and they differ

```
Not directly comparable — judge version changed from rubric-2026-08-14 to rubric-2026-09-01.
Fixed / Regressed / Improved / Degraded / Unchanged are suppressed: a verdict on
these deltas would be about the rubric, not the model.

Scores only, no verdict (5)
  escalation_tone_angry          pass → FAIL   0.880  0.550  -0.330
  refund_policy_multi_turn       FAIL → pass   0.420  0.810  +0.390
  ...
New (1)  tool_call_retry_on_timeout
Verdicts suppressed 5   New 1
```

**What is suppressed:** Fixed, Regressed, Improved, Degraded — and Unchanged.

Unchanged is on that list deliberately, and it is the one that surprises people. Two
different rubrics landing on the same score is a coincidence, not a finding. "This case
is unchanged" is exactly as unsupportable a claim across a grader change as "this case
regressed", so Drift does not make it.

**What survives, and why:**

- **New** — whether a case exists in a snapshot does not depend on who graded it. A case
  absent from the baseline is genuinely new no matter what the rubric says.
- **The removed-cases line** — same reasoning.
- **Every case's raw `before`, `after` and `delta`** — printed in the "Scores only, no
  verdict" table. The numbers are facts; only the conclusion drawn from them is
  unavailable. If you know the rubric edit was cosmetic, you can read the deltas and
  judge for yourself. Drift will not do it for you, because it cannot tell the
  difference between a cosmetic edit and a scoring overhaul.

### UNKNOWN — one or both snapshots do not record a judge version

```
warning: neither snapshot records a judge version, so Drift cannot tell whether
the grader changed between them. The verdicts below are unverified — pass
--judge-version to `drift snapshot` so Drift can check them.
```

**The verdicts are still shown.** All six buckets, as normal, with the warning above
them.

This is the case where `drift snapshot` was run without `--judge-version`, so the
manifest carries the literal placeholder `unset`. A snapshot with no manifest at all, or
one whose `judge_version` is missing or not a string, lands here too — an incomplete
snapshot directory still diffs, it just cannot be verified.

**Why UNKNOWN does not suppress.** A mismatch is positive evidence that the grader
changed, and that earns suppression. An unrecorded judge version is an *absence of
evidence* — it is equally consistent with nothing having changed. Suppressing it would
blank the diff for every team that has not yet adopted the flag, on every single run,
and a warning that always fires is one nobody reads by the time a real rubric change
trips it. Over-suppression does not fail safe here; it destroys the signal.

Two wordings, because one case is more suspicious than the other:

| Situation | Message |
|---|---|
| Neither side records one | "neither snapshot records a judge version" |
| One side only | "the baseline snapshot records no judge version; the candidate reports X" |

The one-sided case usually means a team adopted `--judge-version` partway through — and
people tend to start recording the rubric version *because* they just changed it. It is
still UNKNOWN, but it is worth reading twice.

## Getting out of UNKNOWN

Pass `--judge-version` to `drift snapshot`:

```bash
drift snapshot --results-file results.json --judge-version rubric-2026-08-14/sha256:3ab91f
```

Any stable string works. What matters is that it changes when your grader changes:

- a git hash or tag of the rubric file
- a content hash of the rubric or the assertion set (the promptfoo adapter uses a
  sha256 over the distinct `assert[]` entries, since in promptfoo the assertions *are*
  the grader)
- a dated version you bump by hand

A value that never changes is worse than `unset`, because it turns a warning into a
false all-clear.

## Exit codes

`drift diff` exits 0 in all three states, including MISMATCH. It is a reporting command:
its job is to show you the comparison, not to judge it, and that has not changed.

**Gate on `drift ci`, not on `drift diff`.** `drift ci` runs the same comparison and does
carry exit-code semantics — `0` when the gate passes, `1` when it fails — and a MISMATCH
fails it:

```
The gate cannot pass: with the rubric changed, a clean diff is not evidence that
nothing broke. Re-snapshot the baseline under the new judge version, then
re-run.
```

That is the point of the state existing. A rubric change makes the comparison
uninterpretable in both directions, so a clean diff is not evidence of safety, and a gate
that passed on one would be worse than a gate that failed. See
[`ci-integration.md`](ci-integration.md).
