# Drift (`getdrift`) — Build Spec (Phases 0–3)

Plug-and-play, repo-embedded regression tool for LLM/agent evals, called Drift. CLI package installs as `getdrift`; the command itself is `drift`. Stores immutable eval snapshots keyed by git commit hash, diffs any two snapshots into a Fixed/Regressed/Improved/Degraded/Unchanged/New bucket report.

Scope of this spec: Phase 0 (skeleton) through Phase 3 (plug-and-play ingestion). Phase 4 (CI gate, trend view, docs) is out of scope — depends on these phases being done and validated first.

Stack: Python, Typer (CLI), standard `git` CLI calls for hash reading, no external DB — filesystem-based storage under `.drift/`. Package name on PyPI: `getdrift`. CLI command: `drift`.

---

## Sequencing (hard dependency order)

```
Agent 1: Task 1 → Task 2 (schema) ──┬──→ Agent 1: Task 3 → Task 4
                                      └──→ Agent 3: Task 1 → Task 2 → Task 3 (parallel)
Agent 1 finishes Task 4 ──────────────→ Agent 2: Task 1 → Task 2
```

- Agent 1's Task 2 (the results.json schema) is the contract everything else depends on. Nothing else starts until it's reviewed and merged.
- Agent 3 only needs the schema, not the finished diff engine — legitimate parallel track once schema lands.
- Agent 2 needs Agent 1's complete diff engine (Task 4) before it can add variance logic on top — must run last, not parallel.
- Every task ends with a human review checkpoint before merge. No agent merges its own work.

---

## Agent 1 — Core Builder

### Task 1: CLI scaffold
- Typer-based CLI with stub commands: `drift init`, `drift snapshot`, `drift diff`
- `drift init` creates this folder structure in the repo root:
```
.drift/
  config.yaml
  golden_set/
  snapshots/
```
- `config.yaml` stub fields: `golden_set_path`, `scorer_config`, `model_config` (leave values empty/commented, just define keys)
- Package should be pip-installable locally (`pip install -e .`) — package name `getdrift`, entry point registers the `drift` command
- Deliverable: repo skeleton, `drift init` runs and creates the folder structure, `drift --help` lists all three stub commands

### Task 2: results.json schema (THE CONTRACT — highest scrutiny task)
- Define as a standalone JSON Schema file at `.drift/schema/results.schema.json`, not just inline in code
- Required fields per eval case entry:
  - `case_id` (string, unique within a run)
  - `metric_scores` (object, metric name → numeric score)
  - `pass` (boolean)
  - `environment` (enum: `golden_set` | `production_sample`)
  - `timestamp` (ISO 8601 string)
- Manifest file (`manifest.json`, one per snapshot) required fields:
  - `commit_hash` (string, from `git rev-parse HEAD`)
  - `created_at` (ISO 8601 string)
  - `model_version` (string, free text — whatever the team's harness reports)
  - `prompt_version` (string, free text)
  - `judge_version` (string — hash or version tag of the scoring rubric/judge used; required even if placeholder for now, Agent 2 depends on this field existing)
- Deliverable: schema file + a short markdown doc explaining each field, with one example valid `results.json` and one example `manifest.json`
- This task requires explicit human sign-off before any other task starts. Flag it clearly when done.

### Task 3: `drift snapshot`
- Reads current commit hash via `git rev-parse HEAD`
- Accepts a results file conforming to the Task 2 schema as input (for now, a `--results-file` flag pointing to a JSON file — real harness integration comes in Agent 3's work)
- Validates the input against the schema; reject with a clear error if invalid
- Writes to `.drift/snapshots/<commit_hash>/manifest.json` and `.drift/snapshots/<commit_hash>/results.json`
- Must never overwrite an existing snapshot directory — if `<commit_hash>` already exists, error out (immutability requirement)
- Deliverable: `drift snapshot --results-file <path>` produces a correctly structured, validated, immutable snapshot

### Task 4: `drift diff`
- `drift diff <hash1> <hash2>` reads both snapshots' `results.json`
- Matches cases by `case_id` across both
- Buckets each case:
  | Bucket | Condition |
  |---|---|
  | Fixed | hash1 fail → hash2 pass |
  | Regressed | hash1 pass → hash2 fail |
  | Improved | both pass, score delta > threshold (configurable, default 0.05) |
  | Degraded | both pass, score delta < -threshold |
  | Unchanged | delta within threshold |
  | New | case_id not present in hash1 |
- Output: colored terminal table, Regressed rows highlighted first/red, Fixed rows green, grouped by bucket
- Deliverable: `drift diff <hash1> <hash2>` produces a correct bucketed table against two real snapshots created via Task 3

---

## Agent 2 — Reliability Engineer
(Starts only after Agent 1 Task 4 is merged)

### Task 1: Noise-aware diffing
- Extend `drift snapshot` to accept N repeated runs per case (config value in `config.yaml`, default N=3)
- Store all N scores per case in `results.json` (extend schema: `metric_scores` becomes a list of N score-objects per case, or add a `runs` array — Agent 2 proposes the exact shape, human reviews before implementing since this changes Agent 1's schema)
- `drift diff` computes mean score per case and a variance/stddev
- A case only counts as Regressed/Improved if the delta exceeds a noise threshold (e.g., delta > 2×combined stddev), not just the raw threshold from Agent 1 Task 4
- Deliverable: diff output distinguishes real regressions from sampling noise on a golden set run with intentionally noisy (non-deterministic) scoring

### Task 2: Judge/rubric versioning enforcement
- At diff time, compare `judge_version` field (from Task 2 manifest schema) between the two snapshots being compared
- If they differ, output a clear warning/flag: "Not directly comparable — judge version changed from X to Y" instead of silently reporting Fixed/Regressed
- Deliverable: `drift diff` correctly flags judge-version mismatches and suppresses false regression/improvement claims in that case

---

## Agent 3 — Integration Engineer
(Starts once Agent 1 Task 2 schema is merged; runs in parallel with Agent 2)

### Task 1: OTel span listener
- Define a span-attribute convention: what OTel span attributes map to which results.json fields (`case_id`, `metric_scores`, `pass`, `environment`)
- Write a listener that subscribes to OTel spans matching this convention and converts them into a valid `results.json` entry
- Deliverable: a documented spec (attribute names) + working listener that can ingest a manually-crafted test span and produce valid schema-conformant output

### Task 2: promptfoo adapter
- Write a small adapter that takes promptfoo's native output format and converts it into the Task 2 schema
- Deliverable: run promptfoo on a sample eval set, adapter produces valid `results.json`, feeds into `drift snapshot` successfully end-to-end

### Task 3: pytest-plugin shim
- Package Drift's snapshot-triggering as a pytest plugin via `entry_points` in `setup.py`/`pyproject.toml`
- On `pytest` invocation (for repos using pytest-style eval runners), the plugin should auto-trigger a snapshot after eval tests run, with zero additional code in the user's test files
- Deliverable: a sample pytest-based eval repo where installing the plugin (pip install only, no code changes) results in an automatic snapshot after `pytest` runs

---

## Review checkpoints (human, not agent)

1. After Agent 1 Task 2 — schema sign-off, blocks everyone else
2. After Agent 1 Task 4 — core loop working end to end on a real example
3. After Agent 2 Task 1 — schema change review (runs array / N-scores shape)
4. After Agent 3 Task 2 — first real external-harness integration proof point
5. Final: all three agents' work merged, run the full flow once manually top to bottom before calling Phases 0–3 done

## Explicitly out of scope for this spec
- CI gate (`drift ci`)
- Trend view (multi-snapshot history plotting)
- HTML report generation
- Live production-traffic diffing beyond the `environment` field existing in the schema
- Any monetization, licensing, or hosted component
