# Drift (`getdrift`) — Build Spec, Phase 4

Phases 0–3 are done: schema, snapshot, diff (with noise-aware thresholding and judge-version comparability), OTel adapter, promptfoo adapter, pytest plugin. 183 tests passing, verified working end-to-end.

This phase: CI gate, trend view across full snapshot history, docs, and one carried-over fix from review.

Stack unchanged: Python, Typer, `rich` for terminal output, filesystem storage under `.drift/`.

---

## Sequencing

```
Agent 1: Task 0 (carried-over fix) → Task 1 (CI gate)
Agent 2: Task 1 (trend data model) → Task 2 (trend CLI output)   [starts after Agent 1 Task 1 merged]
Agent 3: Task 1 (docs)                                            [starts once Agent 1 Task 1 + Agent 2 Task 2 merged]
```

Trend view reuses the existing pairwise `compare()` logic in `diffing.py` run repeatedly across snapshot history — no new comparison logic, just orchestration across N snapshots instead of 2. Keep it that way; do not reimplement bucketing for trend view.

---

## Agent 1 — CI Gate + Carried-Over Fix

### Task 0: Fix suppressed-diff visual styling (small, do first)
- File: `src/getdrift/commands/diff_cmd.py`, function `_filtered_note`
- Current bug: the note explaining what the noise filter withheld is styled `[dim]...[/dim]` — the same visual weight as `Unchanged` rows. The docstring's own stated intent is "this is the line that stops them looking like nothing happened," but `dim` styling does the opposite: it visually deprioritizes the exact line meant to draw attention.
- Fix: change `[dim]{...}[/dim]` to `[yellow]{...}[/yellow]` in `_filtered_note` (matches the existing `yellow` used for provenance mismatches elsewhere in the same file — stay consistent with that palette, don't introduce a new color).
- Deliverable: suppressed-case notes render in yellow, existing tests still pass, add one test asserting the note is not styled `dim`.

### Task 1: `drift ci` command
- New command: `drift ci --baseline <hash> --current <hash> [--fail-on regression] [--threshold N] [--noise-sigma N]`
- If `--current` omitted, default to current `HEAD` (or the dirty-tree snapshot if working tree is dirty — reuse existing `has_uncommitted_changes()` logic, do not duplicate it)
- If `--baseline` omitted, default to the most recent snapshot on the default branch (read from `.drift/config.yaml`, add a `default_branch` config key, default `main`)
- Runs the same `compare()` logic as `drift diff`, same bucketing
- `--fail-on regression` (default): exit code 1 if the Regressed bucket is non-empty
- `--fail-on degraded`: exit code 1 if Regressed OR Degraded is non-empty (stricter mode)
- Must also exit non-zero (with a clear message, not silent) if comparability check returns MISMATCH — a judge-version change should block CI the same way a real regression would, since the diff can't be trusted either way
- Print the same bucketed table as `drift diff` before exiting, so a CI log shows the actual failure, not just an exit code
- Deliverable: sample GitHub Actions workflow YAML in `examples/ci/github-actions.yml` showing `drift ci` wired into a PR check, plus tests covering: clean pass, regression-triggered failure, judge-mismatch-triggered failure, dirty-tree handling

---

## Agent 2 — Trend View
(Starts once Agent 1 Task 1 is merged — trend view's CI-friendly output mode depends on the same exit-code conventions)

### Task 1: Trend data model
- New module `src/getdrift/trend.py`
- Function that takes a `case_id` (or `--metric` name) and walks all snapshots under `.drift/snapshots/`, ordered by commit timestamp (from each manifest's `created_at`), running the existing pairwise `compare()` between each consecutive pair
- Output: an ordered list of (commit_hash, timestamp, score, pass/fail, bucket-vs-previous) tuples per case — this is the data both the terminal view and any future dashboard would consume
- Explicitly detect and flag two patterns the roadmap called out: slow drift (monotonic decline across 3+ consecutive snapshots that never individually crosses the regression threshold) and flip-flopping (pass/fail alternates 2+ times across the history)
- Deliverable: pure data-layer function, unit tested against a synthetic multi-snapshot history fixture, no CLI/rendering yet

### Task 2: `drift trend` command
- `drift trend <case_id>` — renders the Task 1 data as a `rich` table or sparkline-style terminal view: one row per snapshot, score, pass/fail, delta from previous
- If slow-drift or flip-flop is detected, print a distinct flagged line above the table (same yellow treatment as Task 0's fix — stay consistent, this is the same "don't let it blend into Unchanged" principle)
- `drift trend --metric <metric_name>` for an aggregate view across all cases sharing that metric, not just one case
- Deliverable: working `drift trend` command, tested against the fixture from Task 1

---

## Agent 3 — Docs
(Starts once Agent 1 Task 1 and Agent 2 Task 2 are both merged — docs should describe finished behavior, not a moving target)

### Task 1: README — bring up to industry standard
The current README is functional but was written incrementally across Phases 0–3 as features landed. This task is a full pass against the structure real open-source CLI tools use (promptfoo, Braintrust, ripgrep, httpie are reasonable references), not just an append of new commands.

Required sections, in order:
1. **One-line description** at the very top — what it does, no preamble
2. **Badges** — PyPI version, license, test status (CI badge depends on Agent 1 Task 1's GitHub Actions workflow existing — wire the badge to that)
3. **Why** — the problem (silent regressions/drift across changes, automatic fixes outrunning manual review), 3–4 sentences max, no implementation detail here
4. **Install** — `pip install getdrift`, one line
5. **Quickstart** — the smallest possible real example: `drift init` → `drift snapshot` → make a change → `drift snapshot` again → `drift diff`, actual commands and actual sample output (copy real terminal output, not a mockup)
6. **Commands reference** — one subsection per command (`init`, `snapshot`, `diff`, `ci`, `trend`, `ingest`), each with its flags and one example. This is where `drift ci` and `drift trend` get added.
7. **How it works** — brief: results.json contract, immutable git-hash-keyed snapshots, bucket taxonomy table (Fixed/Regressed/Improved/Degraded/Unchanged/New)
8. **Integrations** — promptfoo, OTel, pytest plugin, one line each with a link to the relevant `docs/*.md`
9. **Comparison to alternatives** — a short, honest paragraph on how this differs from promptfoo/Braintrust/Langfuse (repo-embedded, harness-agnostic via the results.json contract, no hosted component) — do not disparage alternatives, state the actual differentiator
10. **Contributing** — link to `CONTRIBUTING.md` if it exists, or a short "issues and PRs welcome" line if not
11. **License** — name it, link the LICENSE file

Explicitly avoid: marketing language, unverified claims ("blazing fast", "the best"), anything that isn't true of the code as it exists today. Every command shown must actually run as written — copy-paste and verify each snippet before committing it.

Deliverable: `README.md` rewritten to this structure, every example command verified to run against the actual current CLI, badges linked and functional.

### Task 2: Other documentation
- New `docs/ci-integration.md`: how to wire `drift ci` into GitHub Actions / GitLab CI, referencing the example YAML from Agent 1 Task 1
- New `docs/trend-view.md`: what slow-drift and flip-flop detection mean, how to read the output
- Audit existing docs (`schema.md`, `comparability.md`, `otel-convention.md`, `promptfoo-mapping.md`) for anything now stale given Phase 4 additions — fix in place, don't leave contradictions (this was the exact problem caught and fixed at the end of Phase 3, keep that discipline)
- Deliverable: docs accurately describe the full `init`/`snapshot`/`diff`/`ci`/`trend`/`ingest` command set with no stale claims

---

## Explicitly out of scope for this phase
- Any hosted dashboard or UI beyond the terminal
- Live production-traffic trend view (the `environment` field exists in the schema already; wiring a separate production-sample trend path is a future phase, not this one)
- Any monetization, licensing, or gated features
- HTML report generation (was mentioned early in planning, never scheduled into a phase — leave it that way unless explicitly requested)

## Review checkpoints
1. After Agent 1 Task 0 — quick visual check, low risk, fast approve
2. After Agent 1 Task 1 — CI gate correctness is high-stakes (a false-negative here means broken evals ship silently); test the sample GitHub Actions workflow for real before approving
3. After Agent 2 Task 1 — verify the drift/flip-flop detection logic against a hand-checked synthetic case before it's trusted
4. After Agent 3 Task 1 — verify every command shown in the README actually runs as written; a README with one broken copy-paste example undermines trust in the whole project
5. Final — run the full flow manually: multiple snapshots across manufactured history, `drift ci` in a real CI run, `drift trend` output reviewed by eye
