"""getdrift.report — pure rendering plus the two file-layout invariants: `latest.*`
always overwritten, an archived file for a given timestamp+hash never overwritten.
"""

import json
from pathlib import Path

import pytest

from getdrift.diffing import Comparability, compare
from getdrift.report import render_json, render_markdown, write_reports

DEMO = Path(__file__).resolve().parent.parent / "examples" / "demo"

BASELINE_HASH = "4f2a1c9e7b83d05a6f1e2c4b8d90a7e3f5c61b28"
CANDIDATE_HASH = "9e3b7a1c2d4f6081a5c3e7b9d1f2a4c6e8b0d3f5"
CREATED_AT = "2026-09-01T09:41:10Z"
EQUAL = Comparability("equal", "v1", "v1", "")
MISMATCH = Comparability("mismatch", "v1", "v2", "judge version changed from v1 to v2")


@pytest.fixture()
def demo_diffs():
    before = json.loads((DEMO / "baseline.json").read_text())
    after = json.loads((DEMO / "candidate.json").read_text())
    diffs, _removed = compare(before, after)
    return diffs


# --- render_json -----------------------------------------------------------------


def test_json_report_has_the_required_top_level_fields(demo_diffs):
    document = json.loads(
        render_json(demo_diffs, EQUAL, BASELINE_HASH, CANDIDATE_HASH, CREATED_AT)
    )
    assert document["baseline_hash"] == BASELINE_HASH
    assert document["candidate_hash"] == CANDIDATE_HASH
    assert document["created_at"] == CREATED_AT
    assert document["comparability"]["state"] == "equal"


def test_json_report_sorts_every_case_into_its_bucket(demo_diffs):
    document = json.loads(
        render_json(demo_diffs, EQUAL, BASELINE_HASH, CANDIDATE_HASH, CREATED_AT)
    )
    assert [c["case_id"] for c in document["buckets"]["Fixed"]] == ["refund_policy_multi_turn"]
    assert [c["case_id"] for c in document["buckets"]["Regressed"]] == ["escalation_tone_angry"]
    # Multi-metric case: no single blended delta (see the dedicated test below) — the
    # per-metric numbers carry the real deltas instead.
    assert all(m["delta"] < 0 for m in document["buckets"]["Regressed"][0]["per_metric"])


def test_json_report_reuses_casediff_field_names_verbatim(demo_diffs):
    """Not a parallel schema: the case dict must be exactly what `CaseDiff` carries."""
    from dataclasses import fields

    document = json.loads(
        render_json(demo_diffs, EQUAL, BASELINE_HASH, CANDIDATE_HASH, CREATED_AT)
    )
    one_case = document["buckets"]["Regressed"][0]
    for f in fields(demo_diffs[0]):
        assert f.name in one_case


def test_json_report_does_not_blend_a_multi_metric_case_into_one_score():
    before = {
        "cases": [
            {
                "case_id": "c", "pass": True,
                "metric_scores": {"accuracy": 0.9, "tone": 0.5},
            }
        ]
    }
    after = {
        "cases": [
            {
                "case_id": "c", "pass": True,
                "metric_scores": {"accuracy": 0.5, "tone": 0.9},
            }
        ]
    }
    diffs, _ = compare(before, after, threshold=0.05, noise_sigma=0.0)
    document = json.loads(
        render_json(diffs, EQUAL, BASELINE_HASH, CANDIDATE_HASH, CREATED_AT)
    )
    all_cases = [c for cases in document["buckets"].values() for c in cases]
    case = next(c for c in all_cases if c["case_id"] == "c")
    assert case["score_before"] is None and case["score_after"] is None
    assert {m["metric"] for m in case["per_metric"]} == {"accuracy", "tone"}


def test_json_report_keeps_environment_mismatch_cases_out_of_the_six_buckets():
    before = {"cases": [{"case_id": "c", "pass": True, "environment": "golden_set",
                          "metric_scores": {"accuracy": 0.9}}]}
    after = {"cases": [{"case_id": "c", "pass": True, "environment": "production_sample",
                         "metric_scores": {"accuracy": 0.9}}]}
    diffs, _ = compare(before, after)
    document = json.loads(
        render_json(diffs, EQUAL, BASELINE_HASH, CANDIDATE_HASH, CREATED_AT)
    )
    assert all(len(cases) == 0 for cases in document["buckets"].values())
    assert [c["case_id"] for c in document["environment_mismatches"]] == ["c"]


# --- render_markdown ---------------------------------------------------------------


def test_markdown_header_carries_both_hashes():
    md = render_markdown([], EQUAL, BASELINE_HASH, CANDIDATE_HASH, CREATED_AT)
    assert BASELINE_HASH[:12] in md.splitlines()[0]
    assert CANDIDATE_HASH[:12] in md.splitlines()[0]


def test_markdown_report_has_a_bucket_table_and_a_section_per_nonempty_bucket(demo_diffs):
    md = render_markdown(demo_diffs, EQUAL, BASELINE_HASH, CANDIDATE_HASH, CREATED_AT)
    assert "| Bucket | Count |" in md
    assert "## Fixed (1)" in md
    assert "refund_policy_multi_turn" in md
    assert "## Unchanged" not in md or "greeting_smoke_test" in md  # Unchanged is non-empty here
    assert "## New (1)" in md


def test_markdown_report_withholds_buckets_on_a_judge_mismatch(demo_diffs):
    md = render_markdown(demo_diffs, MISMATCH, BASELINE_HASH, CANDIDATE_HASH, CREATED_AT)
    assert "Not directly comparable" in md
    assert "## Regressed" not in md and "## Fixed" not in md
    assert "## No verdict" in md
    # New cases are judge-independent and must still get their own section.
    assert "## New (1)" in md


def test_markdown_environment_mismatch_case_gets_its_own_section_and_is_not_lost():
    before = {"cases": [{"case_id": "c", "pass": True, "environment": "golden_set",
                          "metric_scores": {"accuracy": 0.9}}]}
    after = {"cases": [{"case_id": "c", "pass": True, "environment": "production_sample",
                         "metric_scores": {"accuracy": 0.9}}]}
    diffs, _ = compare(before, after)
    md = render_markdown(diffs, EQUAL, BASELINE_HASH, CANDIDATE_HASH, CREATED_AT)
    assert "environment mismatch" in md
    assert "golden_set vs production_sample" in md


def test_markdown_body_has_no_hash_outside_the_header(demo_diffs):
    md = render_markdown(demo_diffs, EQUAL, BASELINE_HASH, CANDIDATE_HASH, CREATED_AT)
    body = "\n".join(md.splitlines()[1:])
    assert BASELINE_HASH[:12] not in body
    assert CANDIDATE_HASH[:12] not in body


# --- write_reports: file layout -----------------------------------------------------


def test_write_reports_writes_latest_and_an_archived_copy(tmp_path, demo_diffs):
    drift = tmp_path / ".drift"
    paths = write_reports(demo_diffs, EQUAL, BASELINE_HASH, CANDIDATE_HASH, CREATED_AT, drift=drift)
    reports = drift / "reports"
    assert (reports / "latest.json").exists()
    assert (reports / "latest.md").exists()
    assert (reports / "2026-09-01T094110Z_9e3b7a1c2d4f.json").exists()
    assert (reports / "2026-09-01T094110Z_9e3b7a1c2d4f.md").exists()
    for path in paths:
        assert path.exists()


def test_write_reports_only_writes_the_requested_formats(tmp_path, demo_diffs):
    drift = tmp_path / ".drift"
    write_reports(
        demo_diffs, EQUAL, BASELINE_HASH, CANDIDATE_HASH, CREATED_AT,
        drift=drift, formats=("json",),
    )
    reports = drift / "reports"
    assert (reports / "latest.json").exists()
    assert not (reports / "latest.md").exists()


def test_write_reports_latest_is_overwritten_every_call(tmp_path, demo_diffs):
    drift = tmp_path / ".drift"
    write_reports(demo_diffs, EQUAL, BASELINE_HASH, CANDIDATE_HASH, CREATED_AT, drift=drift)
    write_reports([], EQUAL, BASELINE_HASH, CANDIDATE_HASH, "2026-09-02T00:00:00Z", drift=drift)
    latest = json.loads((drift / "reports" / "latest.json").read_text())
    assert latest["created_at"] == "2026-09-02T00:00:00Z"
    assert all(len(cases) == 0 for cases in latest["buckets"].values())


def test_write_reports_never_clobbers_an_existing_archived_file(tmp_path, demo_diffs):
    """The real invariant: re-running for the same timestamp+hash must not overwrite
    the archived report, even though `latest` always does."""
    drift = tmp_path / ".drift"
    write_reports(demo_diffs, EQUAL, BASELINE_HASH, CANDIDATE_HASH, CREATED_AT, drift=drift)
    archive = drift / "reports" / "2026-09-01T094110Z_9e3b7a1c2d4f.json"
    original = archive.read_text()
    tampered = original.replace('"equal"', '"tampered"')
    archive.write_text(tampered)

    write_reports([], EQUAL, BASELINE_HASH, CANDIDATE_HASH, CREATED_AT, drift=drift)

    assert archive.read_text() == tampered


def test_write_reports_returns_every_path_it_touched(tmp_path, demo_diffs):
    drift = tmp_path / ".drift"
    paths = write_reports(
        demo_diffs, EQUAL, BASELINE_HASH, CANDIDATE_HASH, CREATED_AT,
        drift=drift, formats=("json", "md"),
    )
    assert len(paths) == 4
    assert all(isinstance(p, Path) for p in paths)


def test_write_reports_rejects_an_unknown_format(tmp_path, demo_diffs):
    with pytest.raises(ValueError):
        write_reports(
            demo_diffs, EQUAL, BASELINE_HASH, CANDIDATE_HASH, CREATED_AT,
            drift=tmp_path / ".drift", formats=("csv",),
        )


def test_write_reports_default_drift_dir_is_the_repo_drift_dir(git_repo, demo_diffs, monkeypatch):
    (git_repo / ".drift").mkdir()
    paths = write_reports(demo_diffs, EQUAL, BASELINE_HASH, CANDIDATE_HASH, CREATED_AT)
    assert all(str(git_repo / ".drift" / "reports") in str(p) for p in paths)
