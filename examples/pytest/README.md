# pytest → Drift, with zero edits to your tests (J9c)

The definition of done for Drift's pytest integration: **a `pip install` alone, with no
changes to any test file, produces a snapshot after `pytest` runs.**

Look at `tests/test_support_agent_evals.py` — an ordinary parametrised pytest suite.
No `import getdrift`, no conftest, no decorator, no `-p` flag. That is the point.

## Run it

```sh
pip install getdrift          # the whole install; pytest finds the plugin via entry_points
cd examples/pytest
git init . && git add -A && git commit -m "eval suite"
drift init                    # opting this repo in to Drift
pytest
```

`pytest` output ends with:

```
------- Drift: snapshot written: .drift/snapshots/<commit>/ (4 case(s)) --------
```

## How a test becomes an eval case

| results.json field | Where it comes from |
|---|---|
| `case_id` | the pytest node id, e.g. `tests/test_support_agent_evals.py::test_agent_answers[refund_policy_multi_turn]` |
| `pass` | whether the test passed |
| `metric_scores.passed` | `1.0` / `0.0` — always present, so any suite is snapshottable with no changes |
| `timestamp` | when the test finished, UTC |
| `metadata` | outcome and duration |

Node ids are a path plus a test name, which is exactly the durable identifier the schema
asks for. Renaming or moving a test renames its case — the same contract every other
Drift adapter has.

## Reporting real scores (optional, still no Drift import)

`record_property` is a **builtin pytest fixture**. Anything under `drift.score.<metric>`
becomes a metric; see `tests/test_scored_evals.py`:

```python
def test_refund_policy_scored(record_property):
    record_property("drift.score.answer_similarity", 0.83)
    record_property("drift.metadata.model", "stand-in")
```

`drift.case_id` and `drift.environment` can be overridden the same way.

## Provenance and switches

Set these as pytest flags or in `pyproject.toml` / `pytest.ini` — never in a test file:

```ini
[tool.pytest.ini_options]
drift_model_version = "claude-opus-5"
drift_prompt_version = "support-agent@v7"
drift_judge_version = "rubric-2026-08-14"   # leaving this unset makes drift diff's
                                            # comparability check meaningless
```

`--no-drift-snapshot` turns it off for a run.

## Skipped, xfail and xpass

A **skipped** test produced no verdict, so it is not a case — and both mechanisms are
treated identically. `@pytest.mark.skip` skips during setup and a runtime
`pytest.skip()` skips during the call phase; if only one were excluded, adding a
`pytest.skip()` would show up in `drift diff` as **Regressed** and removing it as
**Fixed**, which is precisely the false signal Drift exists to suppress.

**xfail is not a skip.** pytest reports it as skipped, but the test really ran and
really failed, so it stays in the snapshot as a failing case with `metadata.xfail` set.
Dropping it would make a known-failing eval silently vanish from the diff. An xpass is
recorded as passing.

## When the plugin does nothing

Deliberately silent, so having Drift installed never disturbs an unrelated suite:

- **no `.drift/` in the repo** — the repo has not run `drift init`, so it is not a Drift repo;
- **the run was interrupted, a usage error, or collected nothing** — the case list would be a partial picture of the suite;
- **a snapshot already exists for this commit** — re-running `pytest` without committing is normal; snapshots are immutable;
- **not a git repo** — a snapshot is keyed to a commit.

A failing test run is still snapshotted. Recording only green runs would defeat the
purpose: the failures are what the next diff needs to see get fixed.

**A snapshot never fails your suite.** Every error path is a warning at worst, including
ones nobody anticipated: losing a snapshot is recoverable, losing a green test run is
not. That holds even under `-W error`, and a `record_property` value that is not JSON
serialisable is kept as its `repr` rather than costing you the snapshot.

Only `SnapshotExistsError` is reported as routine. Anything else that refuses a snapshot
— a judge-version policy rejection, say — is surfaced loudly, so a team that opted into
enforcement cannot quietly stop getting snapshots.
