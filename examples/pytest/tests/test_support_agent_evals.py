"""A plain pytest eval suite. Note what is NOT here: no drift import, no conftest hook,
no decorator, no plugin registration. Drift snapshots this file by being installed."""

import pytest

GOLDEN_SET = [
    ("refund_policy_multi_turn", "Our refund window is 30 days from delivery.", "30 days"),
    ("escalation_tone_angry_customer", "I understand this is frustrating.", "I understand"),
    ("sku_lookup_ambiguous", "Did you mean the 12-pack or the 24-pack?", "12-pack"),
]


def answer(prompt):
    """Stand-in for the agent under test."""
    return prompt


@pytest.mark.parametrize("case_id, prompt, expected", GOLDEN_SET, ids=[c[0] for c in GOLDEN_SET])
def test_agent_answers(case_id, prompt, expected):
    assert expected in answer(prompt)


def test_agent_declines_out_of_scope():
    assert "cannot help" in answer("I cannot help with that.")
