"""Out-of-order protection. Pure.

Every event carries a version taken from the vendor (a sequence number, a revision,
or the event time in milliseconds when the vendor offers nothing better). State for
an entity only ever moves to a strictly greater (version, event_id) pair, so the
final state is the same for every delivery order.

The event id is a tie-break, not an ordering claim: when a vendor emits two events
with the same version, something has to win, and it must be the same one on every
replay.

``CONDITION_EXPRESSION`` is this rule in DynamoDB's language. ``decide`` is the same
rule in Python, used to classify a rejected write and tested against the same cases.
"""

from __future__ import annotations

from enum import Enum

CONDITION_EXPRESSION = (
    "attribute_not_exists(pk) OR #version < :version OR (#version = :version AND last_event_id < :event_id)"
)


class Decision(str, Enum):
    APPLY = "applied"
    STALE = "stale"
    SAME = "already_applied"


def decide(current: tuple[int, str] | None, incoming: tuple[int, str]) -> Decision:
    if current is None or current < incoming:
        return Decision.APPLY
    if current == incoming:
        return Decision.SAME
    return Decision.STALE
