from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from webhook_platform.normalize import MalformedJson, PermanentError, SchemaInvalid, UnknownEventType, normalize
from webhook_tools import plan
from webhook_tools.vendorsim import expected_attributes, iso_ms, render

from .conftest import PEPPER

BASE = datetime(2026, 11, 17, 1, 0, 0, tzinfo=timezone.utc)
CALL_TIME_MS = int(datetime(2026, 11, 17, 1, 2, 3, tzinfo=timezone.utc).timestamp()) * 1000  # whole seconds: exact


def genuine_events() -> list[dict]:
    return [d.event for d in plan.build(3, 300, BASE) if d.category == "valid"]


def body(doc: dict) -> bytes:
    return json.dumps(doc).encode()


MERIDIAN = {
    "id": "evt_1",
    "type": "payment.settled",
    "created": int(BASE.timestamp()),
    "sequence": 2,
    "data": {
        "payment_id": "pay_1",
        "loan_ref": "LA-000001",
        "amount_minor": 125000,
        "currency": "USD",
        "method": "ach",
        "failure_code": None,
    },
}
CALLWRIGHT = {
    "event_uuid": "0a1b2c3d-0000-4000-a000-000000000001",
    "kind": "call.completed",
    "occurred_at": "2026-11-17T01:02:03.123Z",
    "call": {
        "sid": "CW1",
        "direction": "outbound",
        "from": "+15550100101",
        "to": "+1 (415) 555-0123",
        "duration_s": 61,
        "disposition": "answered",
    },
    "context": {"application_id": "LA-000001", "campaign": None},
}
VERITY = {
    "notification_id": "ntf_1",
    "topic": "verification.report.completed",
    "sent_at": "2026-11-17T01:02:03.456Z",
    "report": {
        "report_id": "rpt_1",
        "revision": 3,
        "applicant_ref": "LA-000001",
        "state": "complete",
        "decision": "clear",
        "score_band": "B",
    },
    "subject": {"full_name": "José Núñez", "ssn_last4": "0042", "phone": "+14155550123", "date_of_birth": "1988-03-02"},
}


def test_meridianpay(tokens):
    event = normalize("meridianpay", body(MERIDIAN), tokens)
    assert (event.event_id, event.entity_type, event.entity_id) == ("evt_1", "payment", "pay_1")
    assert (event.status, event.version, event.loan_application_id) == ("settled", 2, "LA-000001")
    assert event.occurred_at == "2026-11-17T01:00:00.000Z"
    assert event.state_key == "payment#pay_1" and event.idempotency_key == "meridianpay#evt_1"
    assert event.attributes["amount_minor"] == 125000


def test_callwright_orders_by_event_time_in_milliseconds(tokens):
    event = normalize("callwright", body(CALLWRIGHT), tokens)
    assert event.version == CALL_TIME_MS + 123
    assert event.occurred_at == "2026-11-17T01:02:03.123Z" and event.event_date == "2026-11-17"
    assert event.status == "completed" and event.attributes["disposition"] == "answered"


def test_epoch_milliseconds_are_exact_for_every_millisecond():
    """Float arithmetic is off by one for some values; ordering depends on this number."""
    from webhook_platform.normalize import _epoch_ms

    start = datetime(2026, 11, 17, 1, 2, 3, tzinfo=timezone.utc)
    for ms in range(1000):
        assert _epoch_ms(start + timedelta(milliseconds=ms)) == CALL_TIME_MS + ms


def test_time_offsets_are_converted_to_utc(tokens):
    doc = {**CALLWRIGHT, "occurred_at": "2026-11-17T03:02:03.123+02:00"}
    assert normalize("callwright", body(doc), tokens).occurred_at == "2026-11-17T01:02:03.123Z"


@pytest.mark.parametrize(("vendor", "doc"), [("callwright", CALLWRIGHT), ("veritybureau", VERITY)])
def test_no_raw_pii_survives_normalisation(vendor, doc, tokens):
    event = normalize(vendor, body(doc), tokens)
    flat = json.dumps(event.to_record(), ensure_ascii=False)
    for raw in ("4155550123", "555-0123", "15550100101", "0042", "José", "Núñez", "1988-03-02"):
        assert raw not in flat
    assert all(
        not name.endswith(("phone", "ssn_last4", "full_name", "date_of_birth", "from", "to"))
        for name in event.attributes
    )


def test_verity_tokens(tokens):
    event = normalize("veritybureau", body(VERITY), tokens)
    assert event.attributes["ssn_last4_token"] == tokens.ssn_last4("0042")
    assert event.attributes["phone_token"] == tokens.phone("+14155550123")
    assert (event.version, event.status) == (3, "complete")


def test_platform_and_generator_agree_on_every_generated_event(tokens):
    """Two implementations, one specification: every genuine event the generator can
    produce normalises to what the verifier expects."""
    events = genuine_events()
    assert len(events) >= 300
    for logical in events:
        for style in range(4):
            event = normalize(logical["vendor"], render(logical, style), tokens)
            assert event.event_id == logical["event_id"]
            assert event.state_key == f"{logical['entity_type']}#{logical['entity_id']}"
            assert (event.version, event.status, event.event_type) == (
                logical["version"],
                logical["status"],
                logical["vendor_type"],
            )
            assert event.occurred_at == iso_ms(logical["occurred_at"])
            assert event.attributes == expected_attributes(logical, PEPPER)


@pytest.mark.parametrize("raw", [b"", b"   ", b"{", b'{"id": "evt_1"', b"<html>502</html>", b"\xff\xfe", b"NaN,"])
def test_unparseable_body_is_malformed_json(raw, tokens):
    with pytest.raises(MalformedJson):
        normalize("meridianpay", raw, tokens)


@pytest.mark.parametrize(
    ("vendor", "doc", "event_type"),
    [
        ("meridianpay", {**MERIDIAN, "type": "payment.disputed"}, "payment.disputed"),
        ("callwright", {**CALLWRIGHT, "kind": "call.transcribed"}, "call.transcribed"),
        ("veritybureau", {**VERITY, "topic": "verification.report.archived"}, "verification.report.archived"),
    ],
)
def test_unknown_event_type(vendor, doc, event_type, tokens):
    with pytest.raises(UnknownEventType) as raised:
        normalize(vendor, body(doc), tokens)
    assert raised.value.reason == "unknown_event_type" and raised.value.detail == event_type


@pytest.mark.parametrize(
    ("vendor", "doc"),
    [
        ("meridianpay", [1, 2, 3]),
        ("meridianpay", {**MERIDIAN, "data": "not an object"}),
        ("meridianpay", {**MERIDIAN, "sequence": "2"}),
        ("meridianpay", {**MERIDIAN, "sequence": -1}),
        ("meridianpay", {**MERIDIAN, "sequence": True}),
        ("meridianpay", {**MERIDIAN, "id": ""}),
        ("meridianpay", {**MERIDIAN, "created": 10**18}),
        ("meridianpay", {k: v for k, v in MERIDIAN.items() if k != "type"}),
        ("callwright", {**CALLWRIGHT, "occurred_at": "17/11/2026"}),
        ("callwright", {**CALLWRIGHT, "occurred_at": "2026-11-17T01:02:03"}),
        ("callwright", {**CALLWRIGHT, "call": {**CALLWRIGHT["call"], "sid": 12345}}),
        ("veritybureau", {**VERITY, "report": {**VERITY["report"], "state": "exploded"}}),
        ("veritybureau", {**VERITY, "subject": {**VERITY["subject"], "ssn_last4": "12345"}}),
        ("veritybureau", {**VERITY, "subject": {**VERITY["subject"], "ssn_last4": "12a4"}}),
        ("nosuchvendor", MERIDIAN),
    ],
)
def test_schema_violations(vendor, doc, tokens):
    with pytest.raises(SchemaInvalid) as raised:
        normalize(vendor, body(doc), tokens)
    assert raised.value.reason == "schema_invalid"


def test_error_detail_never_echoes_payload_values(tokens):
    """Details are logged and stored outside the raw zone."""
    doc = {**VERITY, "subject": {**VERITY["subject"], "ssn_last4": "98765"}}
    with pytest.raises(PermanentError) as raised:
        normalize("veritybureau", body(doc), tokens)
    assert "98765" not in raised.value.detail and "José" not in raised.value.detail


def test_poison_amount_passes_validation_and_cannot_be_stored(tokens):
    """The demo's poison message: structurally valid, impossible to write. Normalising
    succeeds; DynamoDB serialisation refuses numbers beyond 38 digits."""
    import decimal

    from boto3.dynamodb.types import TypeSerializer

    doc = {**MERIDIAN, "data": {**MERIDIAN["data"], "amount_minor": plan.POISON_AMOUNT}}
    event = normalize("meridianpay", body(doc), tokens)
    with pytest.raises((decimal.Inexact, decimal.Rounded)):
        TypeSerializer().serialize(event.attributes)
