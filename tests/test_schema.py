import copy

import pytest

from getdrift.schema import (
    SchemaValidationError,
    validate_manifest,
    validate_results,
)


def test_example_results_is_valid(example_results):
    validate_results(example_results)


def test_example_manifest_is_valid(example_manifest):
    validate_manifest(example_manifest)


def test_invalid_example_reports_every_problem(invalid_results):
    with pytest.raises(SchemaValidationError) as exc:
        validate_results(invalid_results)
    joined = " | ".join(exc.value.problems)
    assert "environment" in joined
    assert "metric_scores" in joined
    assert "pass" in joined
    assert "timestamp" in joined
    assert "duplicate case_id" in joined


@pytest.mark.parametrize(
    "field", ["case_id", "metric_scores", "pass", "environment", "timestamp"]
)
def test_every_required_case_field_is_enforced(example_results, field):
    doc = copy.deepcopy(example_results)
    del doc["cases"][0][field]
    with pytest.raises(SchemaValidationError):
        validate_results(doc)


@pytest.mark.parametrize(
    "field",
    ["commit_hash", "created_at", "model_version", "prompt_version", "judge_version"],
)
def test_every_required_manifest_field_is_enforced(example_manifest, field):
    doc = copy.deepcopy(example_manifest)
    del doc[field]
    with pytest.raises(SchemaValidationError):
        validate_manifest(doc)


def test_judge_version_placeholder_is_accepted(example_manifest):
    doc = copy.deepcopy(example_manifest)
    doc["judge_version"] = "unset"
    validate_manifest(doc)


def test_naive_timestamp_is_rejected(example_results):
    doc = copy.deepcopy(example_results)
    doc["cases"][0]["timestamp"] = "2026-09-01T09:41:02"
    with pytest.raises(SchemaValidationError):
        validate_results(doc)


def test_unknown_case_property_is_rejected(example_results):
    doc = copy.deepcopy(example_results)
    doc["cases"][0]["score"] = 1
    with pytest.raises(SchemaValidationError):
        validate_results(doc)


def test_metadata_is_the_escape_hatch(example_results):
    doc = copy.deepcopy(example_results)
    doc["cases"][0]["metadata"] = {"anything": [1, 2, 3]}
    validate_results(doc)


def test_empty_metric_scores_is_rejected(example_results):
    doc = copy.deepcopy(example_results)
    doc["cases"][0]["metric_scores"] = {}
    with pytest.raises(SchemaValidationError):
        validate_results(doc)


def test_incompatible_major_schema_version_is_rejected(example_results):
    doc = copy.deepcopy(example_results)
    doc["schema_version"] = "2.0.0"
    with pytest.raises(SchemaValidationError):
        validate_results(doc)


# --- version compatibility: a newer file must never be silently misread -------


@pytest.mark.parametrize("version", ["1.0.0", "0.9.0", "1.0.9"])
def test_same_or_older_versions_are_accepted(example_results, version):
    doc = copy.deepcopy(example_results)
    doc["schema_version"] = version
    if version.startswith("1."):
        validate_results(doc)
    else:
        with pytest.raises(SchemaValidationError):
            validate_results(doc)


@pytest.mark.parametrize("version", ["1.2.0", "1.9.0", "1.99.0", "2.0.0"])
def test_a_newer_schema_version_is_rejected_not_ignored(example_results, version):
    """An older Drift must refuse a newer file rather than ignore fields it cannot see."""
    doc = copy.deepcopy(example_results)
    doc["schema_version"] = version
    with pytest.raises(SchemaValidationError) as exc:
        validate_results(doc)
    assert any("schema_version" in problem for problem in exc.value.problems)


def test_newer_minor_names_the_remedy(example_results):
    doc = copy.deepcopy(example_results)
    doc["schema_version"] = "1.2.0"
    with pytest.raises(SchemaValidationError) as exc:
        validate_results(doc)
    assert any("Upgrade Drift" in problem for problem in exc.value.problems)
