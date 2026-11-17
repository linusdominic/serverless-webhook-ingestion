"""What the three vendors do: render an event in their format and sign the request.

Written from the vendor specifications in docs/vendors.md, not from the platform's
verification code.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

VENDORS = ("meridianpay", "callwright", "veritybureau")


@dataclass(frozen=True)
class Key:
    key_id: str
    secret: bytes


def iso_ms(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"


# --- payloads ---------------------------------------------------------------------


def render(event: dict[str, Any], style: int) -> bytes:
    """Vendor JSON for a logical event. ``style`` varies whitespace, key order and
    escaping, because real vendors do, and the archive has to preserve every byte."""
    doc = _DOCUMENT[event["vendor"]](event)
    if style % 4 == 0:
        text = json.dumps(doc, separators=(",", ":"))
    elif style % 4 == 1:
        text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    elif style % 4 == 2:
        text = json.dumps(doc, sort_keys=True, ensure_ascii=False)
    else:
        text = json.dumps(doc, separators=(", ", ": ")) + "\r\n"
    return text.encode("utf-8")


def _meridianpay(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": event["event_id"],
        "type": event["vendor_type"],
        "created": int(event["occurred_at"].timestamp()),
        "sequence": event["version"],
        "data": {
            "payment_id": event["entity_id"],
            "loan_ref": event["loan_application_id"],
            "amount_minor": event["details"]["amount_minor"],
            "currency": event["details"]["currency"],
            "method": event["details"]["method"],
            "failure_code": event["details"]["failure_code"],
        },
    }


def _callwright(event: dict[str, Any]) -> dict[str, Any]:
    details = event["details"]
    call: dict[str, Any] = {
        "sid": event["entity_id"],
        "direction": details["direction"],
        "from": details["from"],
        "to": details["to"],
    }
    if event["vendor_type"] == "call.completed":
        call["duration_s"] = details["duration_s"]
        call["disposition"] = details["disposition"]
    return {
        "event_uuid": event["event_id"],
        "kind": event["vendor_type"],
        "occurred_at": iso_ms(event["occurred_at"]),
        "call": call,
        "context": {"application_id": event["loan_application_id"], "campaign": details["campaign"]},
    }


def _veritybureau(event: dict[str, Any]) -> dict[str, Any]:
    details = event["details"]
    return {
        "notification_id": event["event_id"],
        "topic": event["vendor_type"],
        "sent_at": iso_ms(event["occurred_at"]),
        "report": {
            "report_id": event["entity_id"],
            "revision": event["version"],
            "applicant_ref": event["loan_application_id"],
            "state": event["status"],
            "decision": details["decision"],
            "score_band": details["score_band"],
        },
        "subject": {
            "full_name": details["full_name"],
            "ssn_last4": details["ssn_last4"],
            "phone": details["phone"],
            "date_of_birth": details["date_of_birth"],
        },
    }


_DOCUMENT = {"meridianpay": _meridianpay, "callwright": _callwright, "veritybureau": _veritybureau}


# --- signatures -------------------------------------------------------------------


def sign(vendor: str, body: bytes, key: Key, signed_at: float) -> dict[str, str]:
    """Request headers for ``body`` as the vendor would send them at ``signed_at``."""
    if vendor == "meridianpay":
        timestamp = str(int(signed_at))
        mac = hmac.new(key.secret, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        return {"X-Meridian-Timestamp": timestamp, "X-Meridian-Signature": f"sha256={mac}"}

    if vendor == "callwright":
        request_time = datetime.fromtimestamp(int(signed_at), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        message = b"/webhooks/callwright\n" + request_time.encode() + b"\n" + body
        mac_b64 = base64.b64encode(hmac.new(key.secret, message, hashlib.sha256).digest()).decode()
        return {"X-Callwright-Request-Time": request_time, "X-Callwright-Signature": mac_b64}

    if vendor == "veritybureau":
        millis = str(int(signed_at * 1000))
        message = f"{millis}.{hashlib.sha256(body).hexdigest()}".encode()
        mac = hmac.new(key.secret, message, hashlib.sha512).hexdigest()
        return {"Verity-Signature": f"keyid={key.key_id};ts={millis};sig={mac}"}

    raise ValueError(f"unknown vendor {vendor}")


def effective_signed_at(vendor: str, signed_at: float) -> float:
    """The timestamp as it appears on the wire, after the vendor's rounding."""
    if vendor == "veritybureau":
        return int(signed_at * 1000) / 1000.0
    return float(int(signed_at))


# --- expected values, computed independently of the platform -----------------------


def pii_token(pepper: bytes, kind: str, value: str) -> str:
    return "tok_" + hmac.new(pepper, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()[:32]


def phone_token(pepper: bytes, phone: str) -> str:
    return pii_token(pepper, "phone", "+" + "".join(ch for ch in phone if ch.isdigit()))


def expected_attributes(event: dict[str, Any], pepper: bytes) -> dict[str, Any]:
    """The attributes the curated zone should hold for a logical event."""
    details = event["details"]
    if event["vendor"] == "meridianpay":
        return {
            "amount_minor": details["amount_minor"],
            "currency": details["currency"],
            "method": details["method"],
            "failure_code": details["failure_code"],
        }
    if event["vendor"] == "callwright":
        completed = event["vendor_type"] == "call.completed"
        return {
            "direction": details["direction"],
            "from_phone_token": phone_token(pepper, details["from"]),
            "to_phone_token": phone_token(pepper, details["to"]),
            "campaign": details["campaign"],
            "disposition": details["disposition"] if completed else None,
            "duration_seconds": details["duration_s"] if completed else None,
        }
    return {
        "decision": details["decision"],
        "score_band": details["score_band"],
        "ssn_last4_token": pii_token(pepper, "ssn_last4", details["ssn_last4"]),
        "phone_token": phone_token(pepper, details["phone"]),
    }
