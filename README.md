# Drift (`getdrift`)

Plug-and-play, repo-embedded regression tracking for LLM/agent evals.

Drift stores **immutable** eval snapshots keyed by git commit hash under `.drift/`,
and diffs any two snapshots into six buckets: Fixed, Regressed, Improved,
Degraded, Unchanged, New.

## Install

```bash
pip install -e .
```

## Commands

```bash
drift init                                   # scaffold .drift/ in the repo root
drift snapshot --results-file results.json \
      --model-version claude-opus-5 \
      --prompt-version support-agent@v7 \
      --judge-version rubric-2026-08-14      # snapshot the current commit
drift diff <hash1> <hash2>                   # bucketed diff between two snapshots
```

`hash1`/`hash2` accept an unambiguous prefix. `drift diff --threshold 0.1` overrides the
score delta that counts as Improved/Degraded (default 0.05, or `diff_threshold` in
`.drift/config.yaml`).

## Buckets

| Bucket | Condition |
|---|---|
| Fixed | hash1 fail → hash2 pass |
| Regressed | hash1 pass → hash2 fail |
| Improved | both pass, score delta clears the threshold *and* the noise floor |
| Degraded | both pass, negative delta clears the threshold *and* the noise floor |
| Unchanged | delta clears neither |
| New | `case_id` not present in hash1 |

Score delta is the mean over metrics present in **both** snapshots. "Pass" is the
majority verdict across a case's runs, so one flaky failure is not a regression.

## Noise-aware diffing

An LLM eval scored once is a sample, not a measurement. Run each case N times and put
the runs in the results file, and `drift diff` computes a mean and a standard deviation
per case and requires a change to clear the sampling noise before calling it Improved or
Degraded:

```
noise_floor       = noise_sigma × sqrt(sd_before² + sd_after²)      # default sigma 2.0
effective cutoff  = max(diff_threshold, noise_floor)
```

Cases the floor withholds are still listed, with their real deltas, under a line saying
how many moved and which. Suppressed is not hidden.

A case with no `runs` array is one run: standard deviation 0, floor 0, and therefore
exactly the pre-1.1.0 behaviour. `--noise-sigma 0` turns the floor off entirely.

Set `runs_per_case` (default 3) and `noise_sigma` (default 2.0) in `.drift/config.yaml`.
Drift does not run your evals, so `runs_per_case` is the count it *expects* to find;
`drift snapshot` warns when a case carries fewer.

Worked demonstration, eight cases, four verdicts changed:
[`examples/noisy-golden-set/`](examples/noisy-golden-set/README.md).

## Comparability

Scores are only comparable if they came from the same grader. `drift diff` compares the
two snapshots' `judge_version` and, when it has changed, prints the numbers but
withholds the verdicts — a "regression" across a rubric edit is usually the rubric, not
the model. When neither snapshot records a judge version it warns instead of
suppressing, since that is an absence of evidence rather than evidence of a change.

Pass `--judge-version` to `drift snapshot` so Drift can tell the difference. Full
behaviour in [`docs/comparability.md`](docs/comparability.md).

## Immutability

A snapshot directory is never overwritten. Re-running `drift snapshot` on a commit that
already has one is a hard error, and there is no `--force` — overwriting would make
every past diff unreproducible.

## Try it

```bash
drift init
drift snapshot --results-file examples/demo/baseline.json --judge-version rubric-v1
git commit --allow-empty -m "next commit"
drift snapshot --results-file examples/demo/candidate.json --judge-version rubric-v1
drift diff <first-hash> <second-hash>       # all six buckets, one case each
```

Both snapshots there use `rubric-v1`, so all six buckets are reported. Change one of the
`--judge-version` values and the same diff comes back with the verdicts withheld.

## Docs

- [`docs/schema.md`](docs/schema.md) — the `results.json` / `manifest.json` contract, field by field.
- [`docs/comparability.md`](docs/comparability.md) — when and why `drift diff` withholds a verdict.
- [`examples/noisy-golden-set/`](examples/noisy-golden-set/README.md) — noise-aware diffing, demonstrated.
- [`docs/RELEASE_NOTES.md`](docs/RELEASE_NOTES.md) — **read the `drift init` note before upgrading.**
