# Reading `drift trend`

`drift diff` compares two snapshots. That is the right unit for reviewing a change, and
it is structurally blind to two things — because both are properties of a *sequence*, and
no comparison of two points can see them.

```bash
drift trend <case_id>            # one case across every snapshot
drift trend --metric <name>      # one metric, averaged across every case carrying it
```

`--threshold` and `--noise-sigma` behave exactly as they do for `drift diff`; the trend
view uses the same pairwise comparison between each consecutive pair of snapshots, so a
step is bucketed the same way `drift diff` would bucket it.

Snapshots are ordered by each manifest's `created_at`, not by git history.

---

## Slow drift

A decline where no individual step was ever big enough to be called a regression, so
every diff along the way said Unchanged, correctly.

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

Every step is −0.020, inside the 0.05 threshold. Every `drift diff` along the way was
right to say Unchanged — no single one of them was wrong. Only the whole history shows
the case lost 0.080, and nothing that compares two commits could ever have told you.

**Why the detector exists.** This is the failure mode a per-change gate is built to miss:
a hundred small correct verdicts summing to a regression nobody approved. It is also the
one most likely to arrive from automated changes, where the individual diffs are small by
construction.

### When it flags

Three conditions, **all** required:

1. **The score strictly decreases at every step.** A flat step is not a decline and ends
   the run.
2. **No step in the run was already Degraded or Regressed.** If one was, pairwise diffing
   already reported it — there is nothing hidden to surface, and flagging it again would
   just be noise on top of a verdict you already have.
3. **The run's total drop exceeds the raw threshold.** Without this, a series wobbling
   down by thousandths would flag. If the entire decline is smaller than what a single
   diff would have called noise, there is no hidden regression.

A run must be at least 3 snapshots long. Drift reports the longest qualifying run.

Condition 3 is the one people miss when reading the code, and it is doing real work: it
is the difference between "this case is sliding" and "this case is jittering".

A snapshot the case is **missing** from ends the run rather than being bridged across.
Two declines either side of a gap are two runs, not one — the case was not measured in
between, so nothing is known about what happened there.

## Flip-flopping

A case alternating between pass and fail. It is unstable, not changed.

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

**Why the detector exists.** Look at the `vs previous` column: every adjacent pair
produces a confident Regressed or Fixed. A reviewer seeing any one of those diffs would
believe something real happened — someone would go hunting for the commit that broke
`escalation_tone_angry`, and there isn't one. Only the sequence shows the case has been
flapping the whole time, and that the honest action is to stabilise the case or its
grader rather than bisect for a cause.

### When it flags

Pass/fail alternates two or more times across the history. Snapshots the case is absent
from are **skipped**, not counted as a change: a case that was not run did not fail.

## `--metric`

Averages a metric across every case that carries it:

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

The unstable cases are **named** rather than left inside the average, because averaging is
exactly what would hide them. There is no `vs previous` bucket in this view: a bucket is a
per-case verdict and an average of cases does not have one.

Use `--metric` to see whether a whole eval set is sliding; use the per-case view to find
out which case is responsible.

## Reading the columns

| Column | Meaning |
|---|---|
| `commit` | The commit the snapshot was keyed to. |
| `created_at` | When the snapshot was **written** — not when the case was evaluated. The per-case `timestamp` is a different field; see [`schema.md`](schema.md). |
| `score` | Mean over the metrics present in that snapshot. With a `runs` array, the mean across runs. |
| `pass` | The case's verdict. With runs, the majority verdict. |
| `delta` | Change from the previous snapshot the case appears in. |
| `vs previous` | The bucket `drift diff` would assign to that step. |

The header line reports coverage — `5 of 5 snapshots` — so a case that only appears in
part of the history says so instead of looking like a shorter project.

## What it does not do

Neither detector is a gate. `drift trend` reports; it does not exit non-zero, and
`drift ci` does not consult it. Slow drift and instability are judgement calls about
whether a case is still worth trusting, and neither has a threshold that would be honest
to fail a build on.
