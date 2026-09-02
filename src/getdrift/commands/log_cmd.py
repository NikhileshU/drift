"""`drift log` — one line per snapshot, newest first.

`drift diff` answers "what changed". This answers the question someone asks before
that one even occurs to them: "what do I have". Today the only way to find out is
`ls .drift/snapshots` and squint at hashes — a CLI that sends you to the filesystem
to read its own state is the bug this command exists to fix.
"""

import subprocess
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from getdrift.commands import fail, warn_if_schemas_stale
from getdrift.gitutil import GitError
from getdrift.paths import drift_dir
from getdrift.snapshot import Snapshot
from getdrift.trend import load_history

#: Same convention as A7a / P4-A3: a fact a reader must not miss carries a plain-ASCII
#: marker, because colour does not survive a CI log and this command is read there too.
UNDATED_MARKER = "UNDATED:"


def _subject(commit_hash: str) -> Optional[str]:
    """The commit's first message line, or None if the commit is gone from history.

    A rebase or a shallow clone can leave a snapshot pointing at a commit git no
    longer has. One missing subject must not take down the whole listing — the same
    reason `load_history` skips an unreadable snapshot rather than aborting the walk.
    """
    try:
        proc = subprocess.run(
            ["git", "log", "-1", "--format=%s", commit_hash],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _counts(snapshot: Snapshot) -> str:
    cases = snapshot.results.get("cases", [])
    passed = sum(1 for case in cases if case.get("pass"))
    return f"{passed}/{len(cases)} pass"


def log(ctx: typer.Context) -> None:
    """List every snapshot, newest first: hash, commit subject, timestamp, pass/fail."""
    try:
        drift = drift_dir()
    except GitError as exc:
        fail(exc)
    warn_if_schemas_stale(drift)

    history = load_history(drift)
    if not history:
        fail("no snapshots in .drift/snapshots/ — run `drift snapshot` first")

    table = Table(header_style="bold", border_style="dim")
    table.add_column("commit")
    table.add_column("subject")
    table.add_column("created_at")
    table.add_column("cases", justify="right")

    for snapshot in reversed(history):  # newest first
        subject = _subject(snapshot.commit_hash)
        table.add_row(
            snapshot.commit_hash[:12],
            subject if subject is not None else "[dim]—[/dim]",
            (snapshot.manifest or {}).get("created_at") or "—",
            _counts(snapshot),
        )

    console = Console(highlight=False)
    console.print(table)

    undated = [s.commit_hash for s in history if not (s.manifest or {}).get("created_at")]
    if undated:
        console.print(
            f"[yellow]{UNDATED_MARKER} {len(undated)} snapshot(s) have no readable "
            f"manifest, so they have no timestamp to order by and are shown oldest: "
            f"{', '.join(c[:12] for c in undated)}.[/yellow]"
        )
