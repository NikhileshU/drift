"""J8: the promptfoo adapter, against a real promptfoo 0.122.2 output file."""

import json
from pathlib import Path

import pytest

from getdrift.adapters.promptfoo import PromptfooFormatError, convert, convert_file
from getdrift.schema import validate_results

PROMPTFOO_OUT = Path(__file__).resolve().parent.parent / "examples" / "promptfoo" / "out.json"


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


def test_case_id_gains_the_axes_that_vary(promptfoo_output):
    """A second provider makes description alone ambiguous, so the provider is appended."""
    rows = promptfoo_output["results"]["results"]
    second_provider = json.loads(json.dumps(rows[0]))
    second_provider["provider"] = {"id": "openai:gpt-4o", "label": ""}
    promptfoo_output["results"]["results"] = rows + [second_provider]

    ids = [case["case_id"] for case in convert(promptfoo_output)["cases"]]
    assert ids == [
        "refund_policy_multi_turn::echo",
        "escalation_tone_angry_customer::echo",
        "refund_policy_multi_turn::openai:gpt-4o",
    ]


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
