# The noisy golden set

A deliberately non-deterministic eval set, used to show that noise-aware diffing keeps
real regressions and drops sampling noise. Eight cases, three runs each, scores sampled
from a gaussian per run with the seed pinned so the committed files are reproducible.

Regenerate with `python generate.py`; check the committed files still prove the point
with `python generate.py --verify`.

## What each case shows

`generate.py --verify` asserts both columns, so this table is checked, not claimed. The
"pre-A5" column is a reference implementation of the old rule — raw threshold on
`metric_scores`, harness `pass` taken as given — kept in `generate.py` so "what would
have happened" is executable rather than a story.

| case | pre-A5 | noise-aware | delta | floor | what it shows |
|---|---|---|---|---|---|
| `real-drop-clean` | Degraded | **Degraded** | −0.303 | 0.022 | a real regression with low variance survives |
| `noise-swing` | Degraded | **Unchanged** | −0.197 | 0.265 | identical distributions, still a false regression before |
| `flaky-pass` | Regressed | **Unchanged** | +0.014 | 0.100 | one flaky run flipping the harness verdict is not a regression |
| `real-fail` | Regressed | **Regressed** | −0.402 | 0.052 | a case that genuinely stopped passing still reports |
| `real-gain-clean` | Improved | **Improved** | +0.195 | 0.039 | a real improvement survives |
| `small-drift-noisy` | Degraded | **Unchanged** | −0.194 | 0.364 | a real drop that N=3 at this variance cannot separate from noise |
| `legacy-n1` | Degraded | **Degraded** | −0.100 | 0.000 | a pre-1.1.0 case with no `runs` diffs exactly as it always did |
| `mixed-n` | Degraded | **Unchanged** | −0.131 | 0.610 | a single-run baseline takes the floor from the noisy side |

Four verdicts change. Three of those four were false.

**`noise-swing` is the headline.** Both sides are drawn from the *same distribution* —
nothing changed, at all — and the sampled means still land 0.197 apart, four times the
0.05 raw threshold. The old rule reports a regression on a case where by construction
there is nothing to report.

**`flaky-pass` is the one that would have stopped a release.** Both sides pass two runs
in three. The harness reports a single sample as the case verdict, so it says pass then
fail, and the old rule calls that Regressed — the loudest bucket, on pure jitter. Drift
takes the majority across runs instead.

**`legacy-n1` and `mixed-n` are the compatibility pair.** `legacy-n1` has no `runs`
array at all, so its standard deviation is 0, its noise floor is 0, and it buckets
exactly as it did before this feature existed. `mixed-n` puts a single-run baseline
against a three-run candidate: the floor comes from the noisy side alone, so the pair
does not silently fall back to the raw threshold just because one side is old.

## `small-drift-noisy` is the honest one

It is a **real** drop, and the filter hides it.

That is the correct answer, not a bug. At three runs with this much per-run variance, a
change that size is not distinguishable from the jitter — the data does not support the
claim, so Drift does not make it. The case is in the fixture deliberately, because a
demonstration that only showed the filter winning would be selling it.

**The remedy is more runs, never a smaller sigma.** Raising `runs_per_case` shrinks the
sampling spread and the floor with it, so a real effect eventually clears it. Lowering
`noise_sigma` just moves the goalposts and brings `noise-swing` back with it — you would
recover this true regression and three false ones along with it.

## Seeing it yourself

```bash
drift snapshot --results-file examples/noisy-golden-set/baseline.json  --judge-version rubric-v1
git commit --allow-empty -m "next"
drift snapshot --results-file examples/noisy-golden-set/candidate.json --judge-version rubric-v1

drift diff <first> <second> --noise-sigma 0    # noise floor off
drift diff <first> <second>                    # noise floor on, default 2.0
```

`--noise-sigma 0` disables the **score** noise floor only. Majority-of-runs for a case's
pass verdict is not a threshold and is always on, so `flaky-pass` reads Unchanged in
both runs; the pre-A5 column in the table above is what covers it, and the test suite
asserts it directly.
