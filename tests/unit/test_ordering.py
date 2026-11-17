from __future__ import annotations

import itertools
import random

import pytest

from webhook_platform.ordering import CONDITION_EXPRESSION, Decision, decide


@pytest.mark.parametrize(
    ("current", "incoming", "expected"),
    [
        (None, (1, "a"), Decision.APPLY),
        ((1, "a"), (2, "a"), Decision.APPLY),
        ((2, "a"), (1, "z"), Decision.STALE),
        ((2, "a"), (2, "a"), Decision.SAME),
        ((2, "a"), (2, "b"), Decision.APPLY),  # tie on version: greater event id wins
        ((2, "b"), (2, "a"), Decision.STALE),
        ((9, "a"), (10, "a"), Decision.APPLY),  # numeric, not lexicographic
    ],
)
def test_decide(current, incoming, expected):
    assert decide(current, incoming) is expected


def fold(events: list[tuple[int, str]]) -> tuple[int, str] | None:
    state = None
    for event in events:
        if decide(state, event) is Decision.APPLY:
            state = event
    return state


def test_every_delivery_order_ends_in_the_same_state():
    events = [(1, "e1"), (2, "e2"), (2, "e3"), (3, "e4"), (3, "e4")]
    finals = {fold(list(order)) for order in itertools.permutations(events)}
    assert finals == {(3, "e4")}


def test_duplicates_and_shuffles_never_change_the_outcome():
    rng = random.Random(7)
    events = [(rng.randint(1, 50), f"e{rng.randint(1, 40)}") for _ in range(200)]
    expected = max(events)
    for _ in range(50):
        delivered = events + rng.choices(events, k=100)
        rng.shuffle(delivered)
        assert fold(delivered) == expected


def test_state_never_moves_backwards():
    rng = random.Random(11)
    state = None
    for _ in range(500):
        event = (rng.randint(1, 30), f"e{rng.randint(1, 9)}")
        if decide(state, event) is Decision.APPLY:
            assert state is None or event > state
            state = event


def test_condition_expression_states_the_same_rule():
    assert "attribute_not_exists(pk)" in CONDITION_EXPRESSION
    assert "#version < :version" in CONDITION_EXPRESSION
    assert "#version = :version AND last_event_id < :event_id" in CONDITION_EXPRESSION
