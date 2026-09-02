# Wiring `drift ci` into CI

`drift ci` runs the same comparison as `drift diff` and turns it into an exit code: `0`
when the gate passes, `1` when it fails. It prints the full bucketed table first, so a CI
log shows what broke rather than just a red X.

```bash
drift ci --baseline <hash> --current <hash> [--fail-on regression|degraded]
```

Both hashes are optional. `--current` defaults to `HEAD`; `--baseline` defaults to the
newest commit on `default_branch` (from `.drift/config.yaml`, default `main`) that has a
snapshot.

---

## Two workflow files, and they are not interchangeable

The repo contains two GitHub Actions workflows. Confusing them is how someone ends up
with a green check that tests nothing, so:

| File | What it is | Who it is for |
|---|---|---|
| `.github/workflows/ci.yml` | **Drift's own test suite.** Runs `pytest` across Python 3.9/3.11/3.12. It is what the README's test-status badge points at. | Drift's maintainers. |
| [`examples/ci/github-actions.yml`](../examples/ci/github-actions.yml) | **A template you copy.** Gates *your* repo's evals with `drift ci`. | Teams adopting Drift. |

**Copy the second one.** `.github/workflows/ci.yml` tests Drift itself and says nothing
about your evals.

## What the gate needs

Three things, and all three are easy to get wrong:

**1. Full git history.** `drift ci` walks the default branch to find a baseline snapshot,
and a shallow clone does not contain one. Every job that runs the gate needs:

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0
```

Without it the gate fails with `no snapshot found on 'main'` — which looks like a Drift
bug and is really a checkout setting.

**2. A baseline that exists.** The gate compares a pull request against the newest
snapshot on your default branch, so that branch has to *have* snapshots. That is what the
second job in the template does: on every push to `main` it snapshots and commits the
result under `.drift/snapshots/`. Snapshots are immutable and keyed by commit hash, so
this only ever adds a directory.

Until the first baseline lands on your default branch, the gate has nothing to compare
against. Merge the template, let one push to `main` run, and the next pull request has a
baseline.

**3. A real `--judge-version`.** Without it the gate still runs, but it cannot tell "the
model changed" from "the rubric changed" — see [comparability](#a-changed-rubric-blocks-the-gate)
below. Set it from a repository variable or a hash of your rubric file.

## Replacing the eval step

The template's "Run evals" step is a placeholder. It writes a valid one-case
`results.json` so the sample runs unmodified, and it tells you nothing until you swap it
out. Three ready-made replacements:

```yaml
# promptfoo
- run: |
    promptfoo eval -c promptfooconfig.yaml -o out.json
    drift ingest promptfoo out.json -o results.json

# pytest — the plugin snapshots for you, so drop the separate snapshot step
- run: pytest

# your own harness — anything that writes a schema-conformant results.json
- run: python run_evals.py --out results.json
```

The contract is the file, not the harness: see [`schema.md`](schema.md).

## Choosing what fails the build

| `--fail-on` | Fails when |
|---|---|
| `regression` (default) | Any case went pass → fail. |
| `degraded` | The above, **plus** any score drop that clears the threshold with both runs still passing. |

Start with `regression`. It has the least noise and the clearest meaning: something that
used to work does not. Move to `degraded` once your evals are repeated enough runs for
the noise floor to be meaningful — otherwise you are gating on sampling variance. See the
noise-aware diffing section of the [README](../README.md).

`--threshold` and `--noise-sigma` behave exactly as they do for `drift diff`.

## A failing gate

```console
$ drift ci --baseline 15dd05db03fe
...
Regressed 1  Degraded 1  Fixed 1  Improved 1  New 1  Unchanged 1

FAIL — 1 case(s) in Regressed: escalation_tone_angry
$ echo $?
1
```

The table above that line names the case, both scores and the delta, so the log is the
whole diagnosis.

## A changed rubric blocks the gate

If the two snapshots were graded by different judge versions, the gate fails — and it
fails *without* reporting verdicts:

```console
$ drift ci --baseline 923e82b1708b

923e82b1708b → 82642a249d61  threshold 0.05  noise 2.0σ  fail-on regression

judge_version  rubric-2026-08-14 → rubric-2026-09-01
model_version  unset → unset
prompt_version unset → unset

Not directly comparable — judge version changed from rubric-2026-08-14 to
rubric-2026-09-01.
Fixed / Regressed / Improved / Degraded / Unchanged are suppressed: a verdict on
these deltas would be about the rubric, not the model.
...
The gate cannot pass: with the rubric changed, a clean diff is not evidence that
nothing broke. Re-snapshot the baseline under the new judge version, then
re-run.
$ echo $?
1
```

This is deliberate and it is the part teams push back on. A rubric change makes the
comparison meaningless in **both** directions: the regressions might be the rubric, and
so might the clean result. Passing the build on a diff Drift has already said it cannot
interpret would be worse than failing it. The fix is in the message — re-snapshot the
baseline under the new judge version. Full behaviour in
[`comparability.md`](comparability.md).

## GitLab CI

Same three requirements, different syntax. `GIT_DEPTH: 0` is the `fetch-depth: 0`
equivalent and is just as mandatory:

```yaml
variables:
  GIT_DEPTH: 0          # drift ci needs real history to find a baseline

drift-gate:
  image: python:3.12
  script:
    - pip install git+https://github.com/NikhileshU/drift
    - python run_evals.py --out results.json      # replace with your harness
    - drift snapshot --results-file results.json
        --prompt-version "$CI_COMMIT_SHA"
        --judge-version "$JUDGE_VERSION"
    - drift ci
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
```

Keeping the baseline current needs the equivalent of the template's second job: a
pipeline on your default branch that snapshots and commits `.drift/snapshots/`.

## Reading a failure

| Symptom | Cause |
|---|---|
| `no snapshot found on 'main'` | Shallow clone (`fetch-depth: 0` missing), or the default branch has no snapshots yet. |
| `a snapshot for <hash> already exists` | The commit was already snapshotted. Benign on a re-run; the template uses `|| true`. |
| Gate fails with no Regressed cases | A judge-version mismatch. Look at the provenance lines under the header. |
| `no .drift/ directory` | `drift init` has not been run, or was not committed. |
