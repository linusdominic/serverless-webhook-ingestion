"""Vendor payload -> canonical event. Pure.

Two kinds of failure are raised as ``PermanentError`` and end in quarantine: a body
that is not what the vendor's schema says, and an event type this code does not know.
Retrying cannot fix either, so neither is allowed to burn SQS receives and land in
the DLQ, which is reserved for failures nobody predicted.

Validation is deliberately structural (presence and type). It does not try to bound
every value, and the demo's poison message relies on that: an amount that is a valid
integer but too large for DynamoDB passes here and fails at the write.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .pii import Tokenizer

Scalar = str | int | bool | None


class PermanentError(Exception):
    """A payload that will fail the same way on every attempt."""

    reason = "permanent"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        # Never includes payload values: it is logged and stored outside the raw zone.
        self.detail = detail


class MalformedJson(PermanentError):
    reason = "malformed_json"


class SchemaInvalid(PermanentError):
    reason = "schema_invalid"


class UnknownEventType(PermanentError):
    reason = "unknown_event_type"


@dataclass(frozen=True)
class CanonicalEvent:
    vendor: str
    event_id: str
    event_type: str
    entity_type: str
    entity_id: str
    loan_application_id: str
    occurred_at: str  # RFC 3339, UTC, millisecond precision
    version: int
    status: str
    attributes: dict[str, Scalar] = field(default_factory=dict)

    @property
    def state_key(self) -> str:
        return f"{self.entity_type}#{self.entity_id}"

    @property
    def idempotency_key(self) -> str:
        return f"{self.vendor}#{self.event_id}"

    @property
    def event_date(self) -> str:
        return self.occurred_at[:10]

    def to_record(self) -> dict[str, Any]:
        return asdict(self)


def _iso_ms(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _obj(parent: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = parent.get(name)
    if not isinstance(value, dict):
        raise SchemaInvalid(f"{name}: expected object")
    return value


def _str(parent: Mapping[str, Any], name: str, *, max_len: int = 256) -> str:
    value = parent.get(name)
    if not isinstance(value, str) or not value or len(value) > max_len:
        raise SchemaInvalid(f"{name}: expected non-empty string")
    return value


def _opt_str(parent: Mapping[str, Any], name: str, *, max_len: int = 256) -> str | None:
    if parent.get(name) is None:
        return None
    return _str(parent, name, max_len=max_len)


def _int(parent: Mapping[str, Any], name: str) -> int:
    value = parent.get(name)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SchemaInvalid(f"{name}: expected non-negative integer")
    return value


def _time(parent: Mapping[str, Any], name: str) -> datetime:
    text = _str(parent, name, max_len=64)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        raise SchemaInvalid(f"{name}: expected RFC 3339 timestamp") from None
    if parsed.tzinfo is None:
        raise SchemaInvalid(f"{name}: timestamp has no offset")
    return parsed.astimezone(timezone.utc)


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _epoch_ms(ts: datetime) -> int:
    # Integer arithmetic. int(ts.timestamp() * 1000) goes through a float and is off
    # by one for some values, which would silently reorder events.
    return (ts - _EPOCH) // timedelta(milliseconds=1)


_MERIDIAN_STATUS = {
    "payment.initiated": "initiated",
    "payment.settled": "settled",
    "payment.failed": "failed",
    "payment.returned": "returned",
}


def _meridianpay(doc: Mapping[str, Any], _: Tokenizer) -> CanonicalEvent:
    event_type = _str(doc, "type")
    if event_type not in _MERIDIAN_STATUS:
        raise UnknownEventType(event_type[:64])
    data = _obj(doc, "data")
    created = _int(doc, "created")
    try:
        occurred = datetime.fromtimestamp(created, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        raise SchemaInvalid("created: out of range") from None
    return CanonicalEvent(
        vendor="meridianpay",
        event_id=_str(doc, "id"),
        event_type=event_type,
        entity_type="payment",
        entity_id=_str(data, "payment_id"),
        loan_application_id=_str(data, "loan_ref"),
        occurred_at=_iso_ms(occurred),
        version=_int(doc, "sequence"),
        status=_MERIDIAN_STATUS[event_type],
        attributes={
            "amount_minor": _int(data, "amount_minor"),
            "currency": _str(data, "currency", max_len=3),
            "method": _str(data, "method", max_len=32),
            "failure_code": _opt_str(data, "failure_code", max_len=64),
        },
    )


_CALLWRIGHT_STATUS = {
    "call.ringing": "ringing",
    "call.answered": "in_progress",
    "call.completed": "completed",
}


def _callwright(doc: Mapping[str, Any], tokens: Tokenizer) -> CanonicalEvent:
    kind = _str(doc, "kind")
    if kind not in _CALLWRIGHT_STATUS:
        raise UnknownEventType(kind[:64])
    call = _obj(doc, "call")
    context = _obj(doc, "context")
    occurred = _time(doc, "occurred_at")
    attributes: dict[str, Scalar] = {
        "direction": _str(call, "direction", max_len=16),
        "from_phone_token": tokens.phone(_str(call, "from", max_len=32)),
        "to_phone_token": tokens.phone(_str(call, "to", max_len=32)),
        "campaign": _opt_str(context, "campaign", max_len=64),
        "disposition": None,
        "duration_seconds": None,
    }
    if kind == "call.completed":
        attributes["disposition"] = _str(call, "disposition", max_len=32)
        attributes["duration_seconds"] = _int(call, "duration_s")
    return CanonicalEvent(
        vendor="callwright",
        event_id=_str(doc, "event_uuid"),
        event_type=kind,
        entity_type="call",
        entity_id=_str(call, "sid"),
        loan_application_id=_str(context, "application_id"),
        occurred_at=_iso_ms(occurred),
        # This vendor has no sequence number. Event time in milliseconds is the best
        # ordering it offers; ties are broken by event id (see ordering.py).
        version=_epoch_ms(occurred),
        status=_CALLWRIGHT_STATUS[kind],
        attributes=attributes,
    )


_VERITY_TOPICS = {
    "verification.report.created",
    "verification.report.updated",
    "verification.report.completed",
}
_VERITY_STATES = {"pending", "in_review", "complete"}


def _veritybureau(doc: Mapping[str, Any], tokens: Tokenizer) -> CanonicalEvent:
    topic = _str(doc, "topic")
    if topic not in _VERITY_TOPICS:
        raise UnknownEventType(topic[:64])
    report = _obj(doc, "report")
    subject = _obj(doc, "subject")
    state = _str(report, "state", max_len=32)
    if state not in _VERITY_STATES:
        raise SchemaInvalid("report.state: unexpected value")
    ssn_last4 = _str(subject, "ssn_last4", max_len=4)
    if len(ssn_last4) != 4 or not ssn_last4.isdigit():
        raise SchemaInvalid("subject.ssn_last4: expected four digits")
    return CanonicalEvent(
        vendor="veritybureau",
        event_id=_str(doc, "notification_id"),
        event_type=topic,
        entity_type="verification",
        entity_id=_str(report, "report_id"),
        loan_application_id=_str(report, "applicant_ref"),
        occurred_at=_iso_ms(_time(doc, "sent_at")),
        version=_int(report, "revision"),
        status=state,
        # full_name and date_of_birth are dropped: nothing downstream needs them, and
        # the raw archive still has them for the people allowed to read it.
        attributes={
            "decision": _opt_str(report, "decision", max_len=32),
            "score_band": _opt_str(report, "score_band", max_len=8),
            "ssn_last4_token": tokens.ssn_last4(ssn_last4),
            "phone_token": tokens.phone(_str(subject, "phone", max_len=32)),
        },
    )


_NORMALIZERS: dict[str, Callable[[Mapping[str, Any], Tokenizer], CanonicalEvent]] = {
    "meridianpay": _meridianpay,
    "callwright": _callwright,
    "veritybureau": _veritybureau,
}


def normalize(vendor: str, body: bytes, tokens: Tokenizer) -> CanonicalEvent:
    try:
        doc = json.loads(body)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise MalformedJson("body is not valid JSON") from None
    if not isinstance(doc, dict):
        raise SchemaInvalid("body: expected a JSON object")
    try:
        normalizer = _NORMALIZERS[vendor]
    except KeyError:
        raise SchemaInvalid("no normalizer for vendor") from None
    return normalizer(doc, tokens)
