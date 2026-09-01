"""J8: the promptfoo adapter, against a real promptfoo 0.122.2 output file."""

import json
from pathlib import Path

import pytest

from getdrift.adapters.promptfoo import PromptfooFormatError, convert, convert_file
from getdrift.schema import validate_results

EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "promptfoo"
PROMPTFOO_OUT = EXAMPLES / "out.json"
#: The same eval set re-run after a second provider was appended to promptfooconfig.yaml.
PROMPTFOO_OUT_TWO_PROVIDERS = EXAMPLES / "out.two-providers.json"


@pytest.fixture()
def promptfoo_output():
    return json.loads(PROMPTFOO_OUT.read_text())


def test_real_promptfoo_output_converts_to_valid_results():
    results = convert_file(PROMPTFOO_OUT)
    validate_results(results)

    passing, failing = results["cases"]
    assert passing["case_id"] == "refund_policy_multi_turn"
    assert failing["case_id"] == "escalation_tone_angry_customer"
    assert passing["pass"] is True and failing["pass"] is False
    # namedScores (from `metric:` on the assertions) plus the always-present overall score.
    assert passing["metric_scores"] == {"answer_correctness": 1, "verbosity": 1, "score": 1}
    assert failing["metric_scores"]["score"] == 0.1
    # Noisy fields stay out of metric_scores so they cannot fill the Degraded bucket.
    assert "latency_ms" not in failing["metric_scores"]
    assert failing["metadata"]["latency_ms"] >= 0
    assert passing["environment"] == "golden_set"
    assert passing["timestamp"] == "2026-09-01T10:05:45.368Z"
    assert results["metadata"]["harness"] == "promptfoo"


def test_adding_a_provider_adds_cases_and_renames_none():
    """The stability rule, proved on two real promptfoo runs of the same eval set.

    A case_id that already exists must never change: renaming a case silently destroys
    the snapshot history that Drift exists to preserve. Appending a provider to
    promptfooconfig.yaml must therefore ADD cases, not re-key the existing ones.
    """
    before = {case["case_id"] for case in convert_file(PROMPTFOO_OUT)["cases"]}
    after = {case["case_id"] for case in convert_file(PROMPTFOO_OUT_TWO_PROVIDERS)["cases"]}

    assert before == {"refund_policy_multi_turn", "escalation_tone_angry_customer"}
    assert before <= after, "an existing case_id changed when a provider was added"
    assert after - before == {
        "refund_policy_multi_turn::upper",
        "escalation_tone_angry_customer::upper",
    }


def test_anchor_ignores_row_order(promptfoo_output):
    """Rows arrive in completion order, so the anchor must key on promptIdx, not position."""
    rows = promptfoo_output["results"]["results"]
    extra = json.loads(json.dumps(rows[0]))
    extra["provider"] = {"id": "openai:gpt-4o", "label": ""}
    extra["promptIdx"] = 1
    promptfoo_output["results"]["results"] = [extra] + rows  # new provider finishes first

    ids = {case["case_id"] for case in convert(promptfoo_output)["cases"]}
    assert ids == {
        "refund_policy_multi_turn",
        "escalation_tone_angry_customer",
        "refund_policy_multi_turn::openai:gpt-4o",
    }


def test_provider_label_added_later_does_not_rename_a_case(promptfoo_output):
    """case_id keys on the provider id; a label is often unset and may appear later."""
    before = {case["case_id"] for case in convert(promptfoo_output)["cases"]}
    for row in promptfoo_output["results"]["results"]:
        row["provider"]["label"] = "Echo (dev)"
    assert {case["case_id"] for case in convert(promptfoo_output)["cases"]} == before


def test_undescribed_test_falls_back_to_a_vars_digest_not_an_index(promptfoo_output):
    rows = promptfoo_output["results"]["results"]
    rows[0]["testCase"] = {"vars": rows[0]["vars"]}
    first = convert(promptfoo_output)["cases"][0]["case_id"]
    assert first.startswith("test-") and len(first) == len("test-") + 12
    # Stable: the same vars must produce the same id on a later run.
    assert convert(promptfoo_output)["cases"][0]["case_id"] == first


def test_config_with_no_named_metrics_is_still_ingestible(promptfoo_output):
    for row in promptfoo_output["results"]["results"]:
        row["namedScores"] = {}
    for case in convert(promptfoo_output)["cases"]:
        assert set(case["metric_scores"]) == {"score"}


def test_metric_name_the_schema_forbids_is_a_loud_error(promptfoo_output):
    promptfoo_output["results"]["results"][0]["namedScores"] = {"answer correctness": 1}
    with pytest.raises(PromptfooFormatError, match="forbids"):
        convert(promptfoo_output)


def test_non_promptfoo_json_is_rejected():
    with pytest.raises(PromptfooFormatError, match="no `results` array"):
        convert({"nothing": "useful"})


def test_provenance_fills_in_all_three_manifest_fields(promptfoo_output):
    """`unset` judge_version makes drift diff's comparability check useless — avoid it."""
    from getdrift.adapters.promptfoo import provenance

    fields = provenance(promptfoo_output)
    assert fields["model_version"] == "echo"
    assert fields["prompt_version"] == "557aa7663dd9"
    assert fields["judge_version"].startswith("promptfoo-asserts:sha256:")
    assert "unset" not in fields.values()
    # It also rides along in the results.json, so it survives even if the flags are missed.
    assert convert(promptfoo_output)["metadata"]["provenance"] == fields


def test_judge_version_moves_when_the_rubric_changes(promptfoo_output):
    from getdrift.adapters.promptfoo import provenance

    before = provenance(promptfoo_output)["judge_version"]
    promptfoo_output["results"]["results"][0]["testCase"]["assert"][0]["value"] = "45 days"
    assert provenance(promptfoo_output)["judge_version"] != before


def test_judge_version_is_stable_when_only_scores_change(promptfoo_output):
    """Same assertions, different outcomes: the grader did not change, so nor does it."""
    from getdrift.adapters.promptfoo import provenance

    before = provenance(promptfoo_output)["judge_version"]
    for row in promptfoo_output["results"]["results"]:
        row["success"], row["score"], row["namedScores"] = True, 1, {"answer_correctness": 1}
    assert provenance(promptfoo_output)["judge_version"] == before


def test_labelled_prompt_keeps_its_name_and_its_content_hash(promptfoo_output):
    from getdrift.adapters.promptfoo import provenance

    promptfoo_output["results"]["prompts"][0]["label"] = "support-agent"
    assert provenance(promptfoo_output)["prompt_version"] == "support-agent@557aa7663dd9"


def test_multiple_providers_are_recorded_in_config_order_not_completion_order():
    """Rows finish out of order; identical runs must still produce an identical manifest."""
    from getdrift.adapters.promptfoo import provenance

    document = json.loads(PROMPTFOO_OUT_TWO_PROVIDERS.read_text())
    assert provenance(document)["model_version"] == "echo,upper"

    document["results"]["results"].reverse()
    assert provenance(document)["model_version"] == "echo,upper"


def test_a_user_metric_named_score_is_never_overwritten(promptfoo_output):
    """Snapshots are immutable, so clobbering a user's metric would be wrong forever."""
    row = promptfoo_output["results"]["results"][0]
    row["namedScores"], row["score"] = {"score": 0.99}, 0.10

    scores = convert(promptfoo_output)["cases"][0]["metric_scores"]
    assert scores["score"] == 0.99, "the user's own metric keeps its name"
    assert scores["promptfoo_score"] == 0.10, "promptfoo's overall is kept, not dropped"


def test_a_missing_overall_score_is_not_invented(promptfoo_output):
    """0.0 is a real value: injecting one drags the case's delta down for free."""
    row = promptfoo_output["results"]["results"][0]
    row["namedScores"], row["score"] = {"acc": 0.8}, None

    assert convert(promptfoo_output)["cases"][0]["metric_scores"] == {"acc": 0.8}


def test_no_scores_at_all_raises_instead_of_fabricating_one(promptfoo_output):
    for row in promptfoo_output["results"]["results"]:
        row["namedScores"], row["score"] = {}, None
    with pytest.raises(PromptfooFormatError, match="no numeric scores"):
        convert(promptfoo_output)


def test_a_naive_run_timestamp_warns_once_and_falls_back(promptfoo_output, recwarn):
    """One run-level field, so it must not become one schema error per case."""
    promptfoo_output["results"]["timestamp"] = "2026-09-01 10:05:45"

    results = convert(promptfoo_output)  # would raise on validation if it leaked through
    assert len(recwarn.list) == 1
    assert "no explicit UTC offset" in str(recwarn.list[0].message)
    assert all(case["timestamp"].endswith("Z") for case in results["cases"])
