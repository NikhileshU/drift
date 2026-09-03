"""Drift's report writer: JSON and Markdown, standalone from the CLI and the plugin.

A pure rendering + filesystem module. It does not compute diffs (`diffing.compare()`
already owns that) and it does not print to a terminal (`drift diff`/`drift ci` and
the pytest plugin's own summary own that). Two callers need exactly the same bytes for
the same inputs — a CLI `--report` flag and the pytest plugin's auto-export — and two
independent renderers for one report is how they'd eventually disagree, the same
reasoning `case_stats` and `_removed_note` already follow elsewhere in this codebase.

`write_reports()` is the only function another module should call — `render_json` and
`render_markdown` are exported so they can be unit-tested as pure string functions,
without touching a filesystem.
"""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from getdrift.diffing import (
    BUCKET_ORDER,
    ENVIRONMENT_MISMATCH,
    REMOVED_MARKER,
    CaseDiff,
    Comparability,
)
from getdrift.paths import drift_dir

REPORTS_DIRNAME = "reports"


def _timestamp_slug(created_at: str) -> str:
    """`created_at` with `:` and `.` stripped, so a directory listing sorts
    chronologically and every character is filesystem-safe. `created_at` is already
    UTC ISO-8601 (e.g. `2026-09-01T09:41:10Z`, the same shape `manifest.json` writes),
    so this never invents a new timestamp format — it only removes the two characters
    an OS-portable filename can't carry.
    """
    return created_at.replace(":", "").replace(".", "")


def _archive_name(created_at: str, candidate_hash: str, ext: str) -> str:
    """`<timestamp>_<12-char hash>.<ext>` — timestamp first so `ls` sorts by time.

    Keyed on `candidate_hash`, not `baseline_hash`: the report describes what changed
    *arriving at* the candidate, and `created_at` is that snapshot's own timestamp, so
    pairing it with the baseline's hash would name the file after one snapshot's time
    and a different snapshot's identity.
    """
    return f"{_timestamp_slug(created_at)}_{candidate_hash[:12]}.{ext}"


def _num(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:.3f}"


def _delta(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:+.3f}"


def _group_by_bucket(diffs: List[CaseDiff]) -> Dict[str, List[CaseDiff]]:
    """Every case, grouped by its six-bucket verdict. `ENVIRONMENT_MISMATCH` cases
    are excluded — they aren't one of `BUCKET_ORDER`'s six; callers handle them
    separately, the same as `_environment_mismatch_note` does in the CLI.
    """
    buckets: Dict[str, List[CaseDiff]] = {bucket: [] for bucket in BUCKET_ORDER}
    for case in diffs:
        if case.bucket in buckets:
            buckets[case.bucket].append(case)
    return buckets


def _bucket_lists(diffs: List[CaseDiff]) -> Dict[str, List[Dict[str, Any]]]:
    """`_group_by_bucket`, as plain dicts for JSON.

    Reuses `CaseDiff`'s own field names via `asdict` rather than a parallel shape —
    see the module docstring on why two names for one concept is worth avoiding. A
    multi-metric case's `score_before`/`score_after`/`delta` are already `None` on the
    `CaseDiff` itself (nothing upstream blends incompatible metrics into one number —
    see `case_stats`), so this serialises that `None` as-is; the real per-metric
    numbers are always present in `per_metric`, single-metric case or not.
    """
    return {bucket: [asdict(c) for c in cases] for bucket, cases in _group_by_bucket(diffs).items()}


def render_json(
    diffs: List[CaseDiff],
    comparability: Comparability,
    baseline_hash: str,
    candidate_hash: str,
    created_at: str,
    removed: Sequence[str] = (),
) -> str:
    """The report as a JSON string (trailing newline, 2-space indent).

    `environment_mismatches` is separate from `buckets`: `ENVIRONMENT_MISMATCH` is
    deliberately not one of `BUCKET_ORDER`'s six (see `diffing.py`), and dropping
    those cases silently here would be the same loss `_environment_mismatch_note`
    exists to prevent in the CLI. Included regardless of `comparability` — this is
    the full data, not a decision about what a human should be shown; that decision
    is `render_markdown`'s to make. `removed` (case_ids in `before`, gone from
    `after` — `compare()`'s second return value) gets the same treatment: a report
    that cannot express a removed case would silently drop it, same failure family.
    """
    mismatched = [c for c in diffs if c.bucket == ENVIRONMENT_MISMATCH]
    document = {
        "baseline_hash": baseline_hash,
        "candidate_hash": candidate_hash,
        "created_at": created_at,
        "comparability": asdict(comparability),
        "buckets": _bucket_lists(diffs),
        "environment_mismatches": [asdict(c) for c in mismatched],
        "removed": sorted(removed),
    }
    return json.dumps(document, indent=2) + "\n"


def _case_detail(case: CaseDiff) -> str:
    """The before/after half of a case's bullet, without the leading `case_id`."""
    before = "—" if case.pass_before is None else ("pass" if case.pass_before else "FAIL")
    after = "pass" if case.pass_after else "FAIL"
    if len(case.per_metric) > 1:
        metrics = "; ".join(
            f"{m.metric} {_num(m.score_before)}→{_num(m.score_after)} ({_delta(m.delta)})"
            for m in case.per_metric
        )
        return f"{before} → {after} — {metrics}"
    return (
        f"{before} → {after}, "
        f"{_num(case.score_before)} → {_num(case.score_after)} ({_delta(case.delta)})"
    )


def _bullet(case: CaseDiff) -> str:
    return f"- `{case.case_id}`: {_case_detail(case)}"


def _removed_section(removed: Sequence[str], baseline_hash: str, candidate_hash: str) -> List[str]:
    """`## Removed` — present only when `removed` is non-empty; an empty sequence adds
    no section at all, per spec. Reuses `_removed_note`'s own sentence, `REMOVED_MARKER`
    included, rather than inventing new wording for the same fact: two implementations
    of "these cases are gone" is how they eventually disagree (see `case_stats`'s
    docstring for the same reasoning applied to scores instead of prose).
    """
    if not removed:
        return []
    names = ", ".join(f"`{case_id}`" for case_id in sorted(removed))
    return [
        f"## Removed ({len(removed)})\n",
        f"{REMOVED_MARKER} {len(removed)} case(s) present in `{baseline_hash[:12]}` "
        f"and gone from `{candidate_hash[:12]}`: {names}",
        "",
    ]


def render_markdown(
    diffs: List[CaseDiff],
    comparability: Comparability,
    baseline_hash: str,
    candidate_hash: str,
    created_at: str,
    removed: Sequence[str] = (),
) -> str:
    """The report as Markdown, meant to be pasted straight into a PR comment or Slack.

    Hashes live in the header only, per spec — no metadata clutter in the body. The
    `Removed` section is the one exception: it names both hashes again, because
    `_removed_note`'s own sentence does and reusing that sentence verbatim (see
    `_removed_section`) was the point.

    When `comparability.suppresses_verdicts` (a known judge-version change), this
    withholds the same six labels `drift diff`'s `_uncomparable()` withholds and shows
    raw numbers instead: a "Regressed" heading is a claim the tool is not entitled to
    make once the rubric itself may have changed, and a PR comment is read out of
    context — more likely than an interactive terminal to have that claim taken at
    face value with the caveat unread. `New` cases still get their own section: they
    do not depend on which judge scored them, only on whether they existed before.
    Removed cases don't depend on the judge either — a case's absence isn't a score —
    so that section renders the same way regardless of `comparability`.
    """
    header = f"# Drift report: `{baseline_hash[:12]}` → `{candidate_hash[:12]}`\n"
    lines = [header]

    if comparability.suppresses_verdicts:
        lines.append(f"**Not directly comparable** — {comparability.detail}.\n")
        lines.append(
            "Fixed / Regressed / Improved / Degraded / Unchanged are withheld: a "
            "verdict on these deltas would be about the rubric, not the model. Raw "
            "scores are shown below with no bucket assigned.\n"
        )
        judged = [c for c in diffs if c.bucket != "New"]
        fresh = [c for c in diffs if c.bucket == "New"]
        if judged:
            lines.append(f"## No verdict ({len(judged)})\n")
            lines.extend(_bullet(c) for c in sorted(judged, key=lambda c: c.case_id))
            lines.append("")
        if fresh:
            lines.append(f"## New ({len(fresh)})\n")
            lines.extend(_bullet(c) for c in sorted(fresh, key=lambda c: c.case_id))
            lines.append("")
        lines.extend(_removed_section(removed, baseline_hash, candidate_hash))
        return "\n".join(lines).rstrip() + "\n"

    buckets = _group_by_bucket(diffs)
    lines.append("| Bucket | Count |")
    lines.append("| --- | --- |")
    lines.extend(f"| {bucket} | {len(buckets[bucket])} |" for bucket in BUCKET_ORDER)
    lines.append("")

    for bucket in BUCKET_ORDER:
        cases = buckets[bucket]
        if not cases:
            continue
        lines.append(f"## {bucket} ({len(cases)})\n")
        lines.extend(_bullet(c) for c in sorted(cases, key=lambda c: c.case_id))
        lines.append("")

    mismatched = [c for c in diffs if c.bucket == ENVIRONMENT_MISMATCH]
    if mismatched:
        lines.append(f"## No verdict — environment mismatch ({len(mismatched)})\n")
        lines.extend(
            f"- `{c.case_id}` ({c.environment_before} vs {c.environment_after}): "
            f"{_case_detail(c)}"
            for c in sorted(mismatched, key=lambda c: c.case_id)
        )
        lines.append("")

    lines.extend(_removed_section(removed, baseline_hash, candidate_hash))
    return "\n".join(lines).rstrip() + "\n"


_RENDERERS = {
    "json": (render_json, "json"),
    "md": (render_markdown, "md"),
}


def write_reports(
    diffs: List[CaseDiff],
    comparability: Comparability,
    baseline_hash: str,
    candidate_hash: str,
    created_at: str,
    removed: Sequence[str] = (),
    drift: Optional[Path] = None,
    formats: Sequence[str] = ("json", "md"),
) -> List[Path]:
    """Render and write each format in `formats` to `.drift/reports/`.

    `removed` is `compare()`'s second return value: case_ids present in the baseline
    and gone from the candidate. Passed through to both renderers; see `render_json`
    and `_removed_section` for why it exists at all.

    Two files per format: `latest.<ext>`, always overwritten — the thing to open right
    now — and an archived `<timestamp>_<hash>.<ext>`, never overwritten. Snapshots
    follow the same never-overwritten rule (`SnapshotExistsError` in `snapshot.py`);
    reports follow it for the same reason, one history entry per commit should mean
    one immutable file. Unlike a snapshot write, a pre-existing archive file here is
    not an error — this can run automatically on every test session, including one
    that reruns without a new snapshot, and an exception on the second run would break
    that automation for no new information. So: leave the existing file untouched and
    return its path along with `latest`'s, exactly as if this call had written it.

    Returns every path involved, `latest` then archive, in `formats` order.
    """
    unknown = [fmt for fmt in formats if fmt not in _RENDERERS]
    if unknown:
        raise ValueError(f"unknown report format(s): {', '.join(unknown)} (want json, md)")

    reports_dir = (drift if drift is not None else drift_dir()) / REPORTS_DIRNAME
    reports_dir.mkdir(parents=True, exist_ok=True)

    written: List[Path] = []
    for fmt in formats:
        render, ext = _RENDERERS[fmt]
        content = render(diffs, comparability, baseline_hash, candidate_hash, created_at, removed)

        latest = reports_dir / f"latest.{ext}"
        latest.write_text(content, encoding="utf-8")
        written.append(latest)

        archive = reports_dir / _archive_name(created_at, candidate_hash, ext)
        if not archive.exists():
            archive.write_text(content, encoding="utf-8")
        written.append(archive)

    return written
