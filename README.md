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
drift init                                # scaffold .drift/ in the repo root
drift snapshot --results-file results.json   # snapshot the current commit
drift diff <hash1> <hash2>                # bucketed diff between two snapshots
```
