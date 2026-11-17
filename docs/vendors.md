# Vendor specifications

Three fictional vendors. The names, header names and payload shapes are invented for
this repository; they are modelled on the kinds of convention real vendors use, and
deliberately differ from each other in every way that matters to a receiver.

Two pieces of code implement this page independently: the platform
(`src/webhook_platform/signatures.py`, `normalize.py`) and the load generator and
verifier (`src/webhook_tools/vendorsim.py`). Neither imports the other.

All vendors POST to `/webhooks/<vendor>` with `Content-Type: application/json`.
A receiver must answer 2xx within the vendor's timeout or the delivery is retried.

## meridianpay (payments processor)

| | |
|---|---|
| Headers | `X-Meridian-Timestamp: <unix seconds>`, `X-Meridian-Signature: sha256=<hex>` |
| Signed message | `<timestamp>` + `.` + raw body |
| Algorithm | HMAC-SHA256, hex |
| Key selection | none on the wire; the receiver tries each active key |
| Event id | `id` |
| Entity | `data.payment_id` |
| Ordering | `sequence`, an integer that increases per payment |
| Event time | `created`, unix seconds |

```json
{"id": "evt_3f2a…", "type": "payment.settled", "created": 1794877200, "sequence": 2,
 "data": {"payment_id": "pay_91c0…", "loan_ref": "LA-000123", "amount_minor": 125000,
          "currency": "USD", "method": "ach", "failure_code": null}}
```

Types: `payment.initiated`, `payment.settled`, `payment.failed`, `payment.returned`.

## callwright (telephony)

| | |
|---|---|
| Headers | `X-Callwright-Request-Time: <RFC 3339 UTC>`, `X-Callwright-Signature: <base64>` |
| Signed message | request path + `\n` + request time + `\n` + raw body |
| Algorithm | HMAC-SHA256, base64 |
| Key selection | none on the wire |
| Event id | `event_uuid` |
| Entity | `call.sid` |
| Ordering | none provided. `occurred_at` in milliseconds is used; two events of one call can share a millisecond |
| Event time | `occurred_at`, RFC 3339 with milliseconds |
| PII | `call.from`, `call.to` (phone numbers, sometimes formatted) |

```json
{"event_uuid": "0a1b2c3d-…", "kind": "call.completed", "occurred_at": "2026-11-17T01:02:03.123Z",
 "call": {"sid": "CW5d…", "direction": "outbound", "from": "+15550100101", "to": "+1 (415) 555-0123",
          "duration_s": 182, "disposition": "answered"},
 "context": {"application_id": "LA-000123", "campaign": "welcome"}}
```

Kinds: `call.ringing`, `call.answered`, `call.completed`.

## veritybureau (credit and identity verification bureau)

| | |
|---|---|
| Header | `Verity-Signature: keyid=<id>;ts=<unix milliseconds>;sig=<hex>` |
| Signed message | `<ts>` + `.` + lower-case hex SHA-256 of the raw body |
| Algorithm | HMAC-SHA512, hex |
| Key selection | `keyid` names the key; an unknown id is rejected |
| Event id | `notification_id` |
| Entity | `report.report_id` |
| Ordering | `report.revision`, an integer |
| Event time | `sent_at`, RFC 3339 with milliseconds |
| PII | `subject.full_name`, `subject.ssn_last4`, `subject.phone`, `subject.date_of_birth` |

```json
{"notification_id": "ntf_77aa…", "topic": "verification.report.completed", "sent_at": "2026-11-17T01:02:03.456Z",
 "report": {"report_id": "rpt_c4…", "revision": 3, "applicant_ref": "LA-000123", "state": "complete",
            "decision": "clear", "score_band": "B"},
 "subject": {"full_name": "José Núñez", "ssn_last4": "0042", "phone": "+14155550123", "date_of_birth": "1988-03-02"}}
```

Topics: `verification.report.created`, `.updated`, `.completed`. States: `pending`,
`in_review`, `complete`.

## What all three have in common

- Deliveries are at-least-once. A delivery that is not acknowledged is retried, re-signed
  with a fresh timestamp, for a period measured in days.
- Deliveries are not ordered. Retries alone guarantee that.
- A delivery that was acknowledged is never sent again. There is no "resend" API.
- Signing secrets are rotated by the vendor, on the vendor's schedule, with an overlap
  during which both the old and the new secret are in use.

## Canonical event

Whatever the vendor, the processor produces one shape:

| Field | Meaning |
|---|---|
| `vendor`, `event_id` | identity; unique together |
| `event_type` | the vendor's own type string |
| `entity_type`, `entity_id` | `payment`, `call` or `verification`, and its id |
| `loan_application_id` | the lender's reference carried by every vendor |
| `occurred_at` | RFC 3339 UTC, milliseconds |
| `version` | ordering key (see ADR 0003) |
| `status` | normalised status |
| `attributes` | vendor-specific scalars; PII replaced by tokens or dropped |
