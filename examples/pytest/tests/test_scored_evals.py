"""The same suite, but reporting real metric scores.

`record_property` is pytest's own builtin fixture — still no Drift import. Anything
recorded under `drift.score.<metric>` becomes a metric in the snapshot; `drift.metadata.<key>`
becomes per-case metadata.
"""


def similarity(answer, expected):
    overlap = len(set(answer.lower().split()) & set(expected.lower().split()))
    return round(overlap / max(len(expected.split()), 1), 3)


def test_refund_policy_scored(record_property):
    answer, expected = "Our refund window is 30 days from delivery.", "refund window is 30 days"
    score = similarity(answer, expected)
    record_property("drift.score.answer_similarity", score)
    record_property("drift.metadata.model", "stand-in")
    assert score > 0.8
