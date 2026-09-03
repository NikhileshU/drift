"""P4-A1 — the trend data layer, against a synthetic history a human can check by eye.

The fixture below is written as a literal table on purpose. Checkpoint 3 requires the
drift and flip-flop logic to be verified against a hand-checked case before anyone
trusts it, and that is only possible if the input is readable as a table rather than
assembled by a helper you have to simulate in your head.

Six snapshots, `_` meaning the case is absent from that snapshot. Threshold 0.05.

    case              s1     s2     s3     s4     s5     s6    expected
    slow-drift       .900   .870   .840   .810   .780   .750   SLOW DRIFT (6, -0.150)
    steady           .800   .800   .800   .800   .800   .800   clean
    flip-flop        .500   .500   .500   .500   .500   .500   FLIP-FLOP (4 transitions)
      pass            P      F      P      F      P      P
    one-big-drop     .900   .900   .600   .600   .600   .600   clean (the -0.30 step is Degraded)
    noisy-wobble     .900   .899   .898   .897   .896   .895   clean (total -0.005 <= threshold)
    gap-case         .900   .870    _     .840   .810   .780   SLOW DRIFT (3, -0.060) from s4

Every per-step delta on `slow-drift` is -0.030, comfortably under the 0.05 threshold, so
`drift diff` reports Unchanged at every single step while the case loses 0.150 overall.
That is the regression pairwise diffing cannot see, which is the reason this module
exists.
"""

from pathlib import Path

import pytest

from getdrift.snapshot import Snapshot
from getdrift.trend import case_trend, load_history, metric_trend

THRESHOLD = 0.05

#: case_id -> six scores, `None` meaning absent from that snapshot.
SCORES = {
    "slow-drift":   [0.900, 0.870, 0.840, 0.810, 0.780, 0.750],
    "steady":       [0.800, 0.800, 0.800, 0.800, 0.800, 0.800],
    "flip-flop":    [0.500, 0.500, 0.500, 0.500, 0.500, 0.500],
    "one-big-drop": [0.900, 0.900, 0.600, 0.600, 0.600, 0.600],
    "noisy-wobble": [0.900, 0.899, 0.898, 0.897, 0.896, 0.895],
    "gap-case":     [0.900, 0.870, None,  0.840, 0.810, 0.780],
}
#: Only `flip-flop` alternates; everything else passes throughout.
PASSES = {"flip-flop": [True, False, True, False, True, True]}

COMMITS = [f"{index:040x}" for index in range(1, 7)]
CREATED = [f"2026-09-0{index}T09:00:00Z" for index in range(1, 7)]


def _snapshot(index):
    cases = []
    for case_id, series in SCORES.items():
        score = series[index]
        if score is None:
            continue
        cases.append({
            "case_id": case_id,
            "metric_scores": {"accuracy": score},
            "pass": PASSES.get(case_id, [True] * 6)[index],
            "environment": "golden_set",
            "timestamp": CREATED[index],
        })
    return Snapshot(
        path=Path(f"/nonexistent/{COMMITS[index]}"),
        commit_hash=COMMITS[index],
        results={"schema_version": "1.1.0", "cases": cases},
        manifest={"commit_hash": COMMITS[index], "created_at": CREATED[index],
                  "judge_version": "rubric-v1"},
    )


@pytest.fixture(scope="module")
def history():
    return [_snapshot(index) for index in range(6)]


# --- the two detectors, which are the whole point of the module -------------------


def test_slow_drift_is_flagged_though_every_step_was_unchanged(history):
    trend = case_trend("slow-drift", history)
    assert [p.bucket for p in trend.points[1:]] == ["Unchanged"] * 5
    drift = trend.slow_drift
    assert drift is not None
    assert drift.snapshots == 6
    assert drift.start_commit == COMMITS[0] and drift.end_commit == COMMITS[5]
    assert drift.total_drop == pytest.approx(0.150)
    assert drift.total_drop > THRESHOLD  # the decline pairwise diffing never reported


def test_flip_flopping_is_flagged_with_the_commits_it_flipped_at(history):
    flip = case_trend("flip-flop", history).flip_flop
    assert flip is not None
    assert flip.transitions == 4
    assert flip.at_commits == [COMMITS[1], COMMITS[2], COMMITS[3], COMMITS[4]]


@pytest.mark.parametrize("case_id", ["steady", "one-big-drop", "noisy-wobble"])
def test_clean_cases_are_not_flagged(history, case_id):
    trend = case_trend(case_id, history)
    assert not trend.flagged, f"{case_id} should not be flagged"


def test_a_step_that_already_regressed_is_not_hidden_drift(history):
    """`one-big-drop` fell 0.30 in one step. Pairwise diffing reported it; nothing hidden."""
    trend = case_trend("one-big-drop", history)
    assert trend.points[2].bucket == "Degraded"
    assert trend.slow_drift is None


def test_a_decline_smaller_than_the_threshold_is_not_drift(history):
    """Six monotonically declining snapshots, total -0.005. There is no regression here."""
    trend = case_trend("noisy-wobble", history)
    assert all(
        b.score < a.score for a, b in zip(trend.points, trend.points[1:])
    ), "the wobble really is monotonic — the total drop is what disqualifies it"
    assert trend.slow_drift is None


def test_a_gap_ends_a_run_instead_of_bridging_it(history):
    """The case is absent from s3. The run is the three after it, not all five."""
    trend = case_trend("gap-case", history)
    assert trend.points[2].present is False
    assert trend.points[2].score is None
    drift = trend.slow_drift
    assert drift is not None
    assert drift.snapshots == 3
    assert drift.start_commit == COMMITS[3]
    assert drift.total_drop == pytest.approx(0.060)


def test_flip_flop_ignores_snapshots_the_case_was_absent_from(history):
    """A case that was not run did not fail, so a gap is not a transition."""
    assert case_trend("gap-case", history).flip_flop is None


# --- the series itself ------------------------------------------------------------


def test_points_are_one_per_snapshot_in_history_order(history):
    trend = case_trend("steady", history)
    assert [p.commit_hash for p in trend.points] == COMMITS
    assert [p.created_at for p in trend.points] == CREATED
    assert trend.points[0].bucket is None  # nothing to compare the first one against


def test_scores_and_verdicts_come_from_the_snapshots(history):
    trend = case_trend("flip-flop", history)
    assert [p.score for p in trend.points] == [0.5] * 6
    assert [p.passed for p in trend.points] == PASSES["flip-flop"]


def test_an_unknown_case_yields_absent_points_not_an_error(history):
    trend = case_trend("never-existed", history)
    assert len(trend.points) == 6
    assert all(not p.present and p.score is None for p in trend.points)
    assert not trend.flagged


def test_buckets_match_what_drift_diff_would_say_for_the_same_pair(history):
    """The trend must not have its own opinion; it calls the same compare()."""
    from getdrift.diffing import compare

    trend = case_trend("one-big-drop", history)
    for index in range(1, 6):
        diffs, _ = compare(history[index - 1].results, history[index].results, THRESHOLD)
        expected = next(d.bucket for d in diffs if d.case_id == "one-big-drop")
        assert trend.points[index].bucket == expected


# --- metric aggregate -------------------------------------------------------------


def test_metric_trend_averages_every_case_carrying_the_metric(history):
    trend = metric_trend("accuracy", history)
    assert len(trend.points) == 6
    # s1: .900 .800 .500 .900 .900 .900 over six cases.
    assert trend.points[0].score == pytest.approx(
        (0.900 + 0.800 + 0.500 + 0.900 + 0.900 + 0.900) / 6
    )
    # s3 has five cases: gap-case is absent.
    assert trend.points[2].score == pytest.approx(
        (0.840 + 0.800 + 0.500 + 0.600 + 0.898) / 5
    )


def test_metric_trend_reports_flip_flopping_per_case_not_as_an_average(history):
    trend = metric_trend("accuracy", history)
    assert trend.flip_flopping_cases == ["flip-flop"]
    assert all(p.passed is None and p.bucket is None for p in trend.points)


def test_an_unknown_metric_is_empty_not_an_error(history):
    trend = metric_trend("no-such-metric", history)
    assert all(not p.present and p.score is None for p in trend.points)
    assert not trend.flagged


# --- ordering, on real snapshot directories ---------------------------------------


def test_history_is_ordered_by_created_at_not_by_directory_name(tmp_path):
    """Commit hashes have no order, so writing them out of order must not matter."""
    import json

    # P6-D1: load_history now only accepts real 40-char-hex commit hashes as snapshot
    # directory names (a stray temp dir must not read as a snapshot) — "aaa"/"bbb"/
    # "ccc" no longer qualify, so the fixture uses hash-shaped names instead.
    snapshots = tmp_path / "snapshots"
    for name, created in (("c" * 40, "2026-09-01T00:00:00Z"),
                          ("a" * 40, "2026-09-03T00:00:00Z"),
                          ("b" * 40, "2026-09-02T00:00:00Z")):
        directory = snapshots / name
        directory.mkdir(parents=True)
        (directory / "results.json").write_text(json.dumps({
            "schema_version": "1.1.0",
            "cases": [{"case_id": "c", "metric_scores": {"accuracy": 0.5}, "pass": True,
                       "environment": "golden_set", "timestamp": created}],
        }))
        (directory / "manifest.json").write_text(json.dumps({"created_at": created}))
    assert [s.commit_hash for s in load_history(tmp_path)] == ["c" * 40, "b" * 40, "a" * 40]


def test_a_snapshot_without_a_manifest_sorts_last_and_is_named(tmp_path):
    """It has no timestamp to order by; dropping it silently would hide a snapshot."""
    import json

    # P6-D1: directory names must be real commit hashes now — see the comment in
    # test_history_is_ordered_by_created_at_not_by_directory_name above.
    snapshots = tmp_path / "snapshots"
    for name, created in (("b" * 40, "2026-09-02T00:00:00Z"), ("a" * 40, None)):
        directory = snapshots / name
        directory.mkdir(parents=True)
        (directory / "results.json").write_text(json.dumps({
            "schema_version": "1.1.0",
            "cases": [{"case_id": "c", "metric_scores": {"accuracy": 0.5}, "pass": True,
                       "environment": "golden_set", "timestamp": "2026-09-02T00:00:00Z"}],
        }))
        if created:
            (directory / "manifest.json").write_text(json.dumps({"created_at": created}))
    assert [s.commit_hash for s in load_history(tmp_path)] == ["b" * 40, "a" * 40]
    assert case_trend("c", drift=tmp_path).undated == ["a" * 40]


def test_no_snapshots_directory_is_an_empty_history_not_a_crash(tmp_path):
    assert load_history(tmp_path) == []
    assert case_trend("c", drift=tmp_path).points == []


def test_same_second_snapshots_are_ordered_by_commit_ancestry(git_repo):
    """`created_at` has whole-second precision, so CI routinely ties on it.

    Found by running `drift trend` on a real repo: five snapshots taken inside two
    seconds came back shuffled, because the tiebreak was commit hash — which has no
    order. A shuffled history invents slow drifts and flip-flops that never happened,
    so this is a correctness bug in the data layer, not a cosmetic one.
    """
    import json
    import subprocess

    same_second = "2026-09-01T09:00:00Z"
    commits = []
    for index in range(4):
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", f"c{index}"],
            cwd=git_repo, check=True,
        )
        commits.append(
            subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=git_repo, capture_output=True, text=True
            ).stdout.strip()
        )

    drift = git_repo / ".drift"
    # Written in reverse, so directory iteration order cannot accidentally be right.
    for index, commit in reversed(list(enumerate(commits))):
        directory = drift / "snapshots" / commit
        directory.mkdir(parents=True)
        (directory / "results.json").write_text(json.dumps({
            "schema_version": "1.1.0",
            "cases": [{"case_id": "c", "metric_scores": {"accuracy": 0.9 - index * 0.03},
                       "pass": True, "environment": "golden_set",
                       "timestamp": same_second}],
        }))
        (directory / "manifest.json").write_text(json.dumps({
            "commit_hash": commit, "created_at": same_second, "judge_version": "v1",
        }))

    assert [s.commit_hash for s in load_history(drift)] == commits
    trend = case_trend("c", drift=drift)
    assert [round(p.score, 3) for p in trend.points] == [0.900, 0.870, 0.840, 0.810]
    assert trend.slow_drift is not None
    assert trend.slow_drift.snapshots == 4
