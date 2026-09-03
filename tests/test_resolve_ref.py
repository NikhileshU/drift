"""P5-D1: git-ref resolution in resolve_snapshot, and the never-silent-ambiguity guarantee."""
import subprocess

import pytest
from typer.testing import CliRunner

from getdrift.cli import app
from getdrift.snapshot import SnapshotNotFoundError, create_snapshot, resolve_snapshot
from tests.test_diffing import DEMO

runner = CliRunner()


def _init(repo):
    assert runner.invoke(app, ["init"]).exit_code == 0


def _commit(repo, message):
    (repo / "README.md").write_text(message + "\n")
    subprocess.run(["git", "commit", "-aqm", message], cwd=repo, check=True)


def test_head_resolves(git_repo):
    _init(git_repo)
    snap = create_snapshot(DEMO / "baseline.json")
    assert resolve_snapshot("HEAD") == snap.path


def test_head_tilde_one_resolves_after_a_second_commit(git_repo):
    _init(git_repo)
    first = create_snapshot(DEMO / "baseline.json")
    _commit(git_repo, "v2")
    create_snapshot(DEMO / "candidate.json")
    assert resolve_snapshot("HEAD~1") == first.path


def test_a_branch_name_resolves(git_repo):
    _init(git_repo)
    snap = create_snapshot(DEMO / "baseline.json")
    subprocess.run(["git", "branch", "stable"], cwd=git_repo, check=True)
    assert resolve_snapshot("stable") == snap.path


def test_a_tag_name_resolves_including_an_annotated_tag(git_repo):
    """Annotated tags need peeling to a commit; a lightweight tag needs none."""
    _init(git_repo)
    snap = create_snapshot(DEMO / "baseline.json")
    subprocess.run(["git", "tag", "-a", "v1", "-m", "release"], cwd=git_repo, check=True)
    assert resolve_snapshot("v1") == snap.path


def test_a_full_hash_never_calls_git(git_repo, monkeypatch):
    """A 40-char match is unambiguous by construction — it must not pay for a git call."""
    _init(git_repo)
    snap = create_snapshot(DEMO / "baseline.json")

    def _boom(ref):
        raise AssertionError("resolve_ref was called for a full-hash match")

    monkeypatch.setattr("getdrift.snapshot.resolve_ref", _boom)
    assert resolve_snapshot(snap.commit_hash) == snap.path


def test_ref_resolved_by_git_but_never_snapshotted_is_a_distinct_error(git_repo):
    """Must not read as 'the ref was bad' — the ref was fine, nothing was snapshotted."""
    _init(git_repo)
    create_snapshot(DEMO / "baseline.json")
    _commit(git_repo, "v2")  # a real commit, deliberately never snapshotted
    with pytest.raises(SnapshotNotFoundError) as exc:
        resolve_snapshot("HEAD")
    message = str(exc.value)
    assert "nothing was snapshotted there" in message
    assert "no snapshot for" not in message
    assert "drift log" in message


def test_hash_prefix_and_git_ref_naming_different_snapshots_is_ambiguous(git_repo):
    """The case the card exists for: never silently pick one."""
    _init(git_repo)
    first = create_snapshot(DEMO / "baseline.json")
    _commit(git_repo, "v2")
    second = create_snapshot(DEMO / "candidate.json")

    prefix = first.commit_hash[:8]
    subprocess.run(["git", "branch", prefix, second.commit_hash], cwd=git_repo, check=True)

    with pytest.raises(SnapshotNotFoundError) as exc:
        resolve_snapshot(prefix)
    message = str(exc.value)
    assert first.commit_hash in message
    assert second.commit_hash in message


# --- P8-D1: `ref` is untrusted (a CLI arg, a CI config value, an env var) and must ---
# never resolve outside `snapshots/`. Confirmed by god before assigning the review:
# `resolve_snapshot("..")` and `resolve_snapshot("../snapshots")` returned real paths
# outside `snapshots/`; only the third probe below "worked" by accident (no
# results.json at the traversal target, not because of any containment check).


def test_dotdot_does_not_escape_snapshots_dir(git_repo):
    _init(git_repo)
    create_snapshot(DEMO / "baseline.json")
    with pytest.raises(SnapshotNotFoundError):
        resolve_snapshot("..")


def test_dotdot_snapshots_does_not_resolve_to_the_snapshots_dir_itself(git_repo):
    _init(git_repo)
    create_snapshot(DEMO / "baseline.json")
    with pytest.raises(SnapshotNotFoundError):
        resolve_snapshot("../snapshots")


def test_traversal_to_a_real_directory_elsewhere_is_still_refused(git_repo, tmp_path):
    """The case that must not hold 'by accident': plant a directory with a real
    results.json outside snapshots/ and confirm traversal still can't reach it, not
    just that it happens to 404 the way `../../../../etc/passwd` does."""
    _init(git_repo)
    create_snapshot(DEMO / "baseline.json")

    bait = tmp_path / "bait"
    bait.mkdir()
    (bait / "results.json").write_text((DEMO / "baseline.json").read_text())

    # `snapshots/` is `git_repo/.drift/snapshots`; walk up to tmp_path, then into bait.
    up = "../" * len((git_repo / ".drift" / "snapshots").relative_to(tmp_path).parts)
    with pytest.raises(SnapshotNotFoundError):
        resolve_snapshot(f"{up}bait")


def test_an_absolute_ref_is_refused_not_a_crash(git_repo):
    """A leading `/` used to reach pathlib's glob() as an absolute pattern and raise
    NotImplementedError instead of a normal SnapshotNotFoundError."""
    _init(git_repo)
    create_snapshot(DEMO / "baseline.json")
    with pytest.raises(SnapshotNotFoundError):
        resolve_snapshot("/etc")


def test_unknown_ref_keeps_the_original_wording(git_repo):
    """P5-D2: points at `drift log`, not `ls .drift/snapshots` — the command now exists."""
    _init(git_repo)
    create_snapshot(DEMO / "baseline.json")
    with pytest.raises(SnapshotNotFoundError) as exc:
        resolve_snapshot("totally-not-a-ref-or-hash")
    message = str(exc.value)
    assert "no snapshot for" in message
    assert "drift log" in message
    assert "ls .drift" not in message
