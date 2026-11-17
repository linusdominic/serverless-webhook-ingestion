"""The seeded workload: logical events, and the deliveries that carry them.

``build(seed, events, base_time)`` is a pure function. The same arguments give the
same plan, byte for byte. Only the signing timestamps are decided later, at send
time, because a signature has to be fresh to be accepted.

Every delivery records what should happen to it. That record is the ground truth the
verifier checks the deployed system against.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .vendorsim import render

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
POISON_AMOUNT = 987654321098765432109876543210987654321098765  # 45 digits; DynamoDB holds 38

_NAMES = ["Ana Souza", "José Núñez", "Zoë Brontë", "Nguyễn Thị Hoa", "Liam O'Connor", "Mei-Ling Chen", "Søren Kjær"]
_COMPANY_LINES = ["+15550100101", "+15550100102", "+15550100103", "+15550100104"]
_CAMPAIGNS = ["welcome", "document-chase", "payment-reminder", None]


@dataclass
class Delivery:
    path_vendor: str
    category: str
    signing: str
    body: bytes
    expected_status: int
    expect: str
    event: dict[str, Any] | None = None
    sign_body: bytes | None = None
    reuse_of: int | None = None
    index: int = -1
    uid: int = field(default=-1, repr=False)


def _ms(ts: datetime) -> int:
    return (ts - EPOCH) // timedelta(milliseconds=1)


def _event(
    vendor: str,
    event_id: str,
    vendor_type: str,
    entity_type: str,
    entity_id: str,
    application: str,
    version: int,
    occurred_at: datetime,
    status: str,
    details: dict[str, Any],
) -> dict[str, Any]:
    return {
        "vendor": vendor,
        "event_id": event_id,
        "vendor_type": vendor_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "loan_application_id": application,
        "version": version,
        "occurred_at": occurred_at,
        "status": status,
        "details": details,
    }


def _phone(rng: random.Random) -> str:
    area, exchange, line = rng.randint(201, 989), rng.randint(200, 999), rng.randint(0, 9999)
    if rng.random() < 0.2:
        return f"+1 ({area}) {exchange}-{line:04d}"  # some vendors send formatted numbers
    return f"+1{area}{exchange}{line:04d}"


def _payment(rng: random.Random, application: str, start: datetime) -> list[dict[str, Any]]:
    payment_id = f"pay_{rng.getrandbits(48):012x}"
    details: dict[str, Any] = {
        "amount_minor": rng.randint(5_000, 500_000),
        "currency": "USD",
        "method": rng.choice(["ach", "card", "wire"]),
        "failure_code": None,
    }
    start = start.replace(microsecond=0)  # this vendor reports whole seconds

    def make(sequence: int, kind: str, at: datetime, failure: str | None = None) -> dict[str, Any]:
        return _event(
            "meridianpay",
            f"evt_{rng.getrandbits(64):016x}",
            f"payment.{kind}",
            "payment",
            payment_id,
            application,
            sequence,
            at,
            kind,
            {**details, "failure_code": failure},
        )

    events = [make(1, "initiated", start)]
    second = start + timedelta(seconds=rng.randint(30, 3600))
    if rng.random() < 0.85:
        events.append(make(2, "settled", second))
        if rng.random() < 0.10:
            events.append(make(3, "returned", second + timedelta(seconds=rng.randint(60, 7200)), "R01"))
    else:
        events.append(make(2, "failed", second, rng.choice(["insufficient_funds", "account_closed"])))
    return events


def _call(rng: random.Random, application: str, start: datetime) -> list[dict[str, Any]]:
    sid = f"CW{rng.getrandbits(64):016x}"
    start = start.replace(microsecond=rng.randint(0, 999) * 1000)
    details: dict[str, Any] = {
        "direction": "outbound",
        "from": rng.choice(_COMPANY_LINES),
        "to": _phone(rng),
        "campaign": rng.choice(_CAMPAIGNS),
        "duration_s": 0,
        "disposition": None,
    }

    def make(kind: str, status: str, at: datetime, **extra: Any) -> dict[str, Any]:
        event_id = (
            f"{rng.getrandbits(32):08x}-{rng.getrandbits(16):04x}-4{rng.getrandbits(12):03x}"
            f"-a{rng.getrandbits(12):03x}-{rng.getrandbits(48):012x}"
        )
        return _event("callwright", event_id, kind, "call", sid, application, _ms(at), at, status, {**details, **extra})

    events = [make("call.ringing", "ringing", start)]
    if rng.random() < 0.7:
        answered_at = start + timedelta(milliseconds=rng.randint(3_000, 20_000))
        events.append(make("call.answered", "in_progress", answered_at))
        # One call in twelve hangs up within the same millisecond it was answered.
        # Two events, same version: the tie-break has to pick the same winner every time.
        talk = 0 if rng.random() < 1 / 12 else rng.randint(5_000, 900_000)
        events.append(
            make(
                "call.completed",
                "completed",
                answered_at + timedelta(milliseconds=talk),
                duration_s=talk // 1000,
                disposition="answered",
            )
        )
    else:
        ended = start + timedelta(milliseconds=rng.randint(15_000, 45_000))
        events.append(
            make("call.completed", "completed", ended, disposition=rng.choice(["no_answer", "busy", "voicemail"]))
        )
    return events


def _verification(rng: random.Random, application: str, start: datetime) -> list[dict[str, Any]]:
    report_id = f"rpt_{rng.getrandbits(48):012x}"
    start = start.replace(microsecond=rng.randint(0, 999) * 1000)
    subject = {
        "full_name": rng.choice(_NAMES),
        "ssn_last4": f"{rng.randint(0, 9999):04d}",
        "phone": _phone(rng),
        "date_of_birth": f"{rng.randint(1955, 2004)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}",
    }
    steps = [
        ("verification.report.created", "pending", None, None),
        ("verification.report.updated", "in_review", None, None),
        (
            "verification.report.completed",
            "complete",
            rng.choice(["clear", "clear", "review", "fail"]),
            rng.choice("ABCDE"),
        ),
    ]
    events, at = [], start
    for revision, (topic, state, decision, band) in enumerate(steps[: rng.choice([2, 3, 3])], start=1):
        events.append(
            _event(
                "veritybureau",
                f"ntf_{rng.getrandbits(64):016x}",
                topic,
                "verification",
                report_id,
                application,
                revision,
                at,
                state,
                {**subject, "decision": decision, "score_band": band},
            )
        )
        at += timedelta(milliseconds=rng.randint(20_000, 1_800_000))
    return events


_ENTITY = {"meridianpay": _payment, "callwright": _call, "veritybureau": _verification}
_ESCALATION = {
    "meridianpay": ("payment.returned", "returned"),
    "callwright": ("call.completed", "completed"),
    "veritybureau": ("verification.report.completed", "complete"),
}
_UNKNOWN_TYPE = {
    "meridianpay": "payment.disputed",
    "callwright": "call.transcribed",
    "veritybureau": "verification.report.archived",
}


def _ghost(rng: random.Random, source: dict[str, Any]) -> dict[str, Any]:
    """An event for a real entity that claims to be newer than anything genuine.

    Used for every delivery that must be rejected. If one is ever accepted, the
    entity's state changes and the verifier sees it.
    """
    vendor = source["vendor"]
    vendor_type, status = _ESCALATION[vendor]
    occurred = source["occurred_at"] + timedelta(days=1)
    details = dict(source["details"])
    if vendor == "callwright":
        details.update(disposition="answered", duration_s=1)
        version = _ms(occurred)
    else:
        version = source["version"] + 10
    if vendor == "veritybureau":
        details.update(decision="clear", score_band="A")
    return {
        **source,
        "event_id": f"ghost_{rng.getrandbits(64):016x}",
        "vendor_type": vendor_type,
        "status": status,
        "version": version,
        "occurred_at": occurred,
        "details": details,
    }


def _drop_required_field(body: bytes, vendor: str) -> bytes:
    doc = json.loads(body)
    if vendor == "meridianpay":
        del doc["data"]["payment_id"]
    elif vendor == "callwright":
        doc["call"]["sid"] = 12345  # wrong type
    else:
        doc["report"]["revision"] = "three"  # wrong type
    return json.dumps(doc).encode()


def build(seed: int, events: int, base_time: datetime) -> list[Delivery]:
    if events < 30:
        raise ValueError("at least 30 events are needed for every failure mode to appear")
    rng = random.Random(seed)
    base_time = base_time.astimezone(timezone.utc).replace(microsecond=0)

    # --- logical events, one entity at a time -------------------------------------
    entities: list[list[dict[str, Any]]] = []
    total, number = 0, 0
    vendors = sorted(_ENTITY)
    while total < events:
        number += 1
        vendor = vendors[number % len(vendors)]
        application = f"LA-{(number + 2) // 3:06d}"
        started = base_time - timedelta(seconds=rng.randint(4 * 3600, 26 * 3600))
        entity = _ENTITY[vendor](rng, application, started)
        entities.append(entity)
        total += len(entity)
    genuine = [event for entity in entities for event in entity]

    # --- valid deliveries, with deliberate reordering ------------------------------
    slots: list[tuple[float, Delivery]] = []
    for entity in entities:
        order = list(entity)
        if len(order) > 1 and rng.random() < 0.25:
            order.reverse()  # newest first: the older events must not win
        offset, step = rng.uniform(0, total), rng.uniform(0.5, 40)
        for position, event in enumerate(order):
            signing = "previous_key" if rng.random() < 0.05 else "valid"
            delivery = Delivery(
                event["vendor"], "valid", signing, render(event, rng.randrange(4)), 202, "processed", event
            )
            slots.append((offset + position * step, delivery))
    slots.sort(key=lambda item: item[0])
    plan = [delivery for _, delivery in slots]
    for uid, delivery in enumerate(plan):
        delivery.uid = uid
    valid = list(plan)
    next_uid = len(plan)

    def insert(delivery: Delivery, after: Delivery | None = None) -> None:
        nonlocal next_uid
        delivery.uid = next_uid
        next_uid += 1
        low = plan.index(after) + 1 if after is not None else 0
        plan.insert(rng.randint(low, min(len(plan), low + 250) if after is not None else len(plan)), delivery)

    def share(fraction: float, minimum: int = 3) -> int:
        return max(minimum, round(events * fraction))

    # --- duplicates ----------------------------------------------------------------
    for original in rng.sample(valid, share(0.05)):
        copy = Delivery(
            original.path_vendor, "duplicate_exact", "reuse", original.body, 202, "processed", original.event
        )
        copy.reuse_of = original.uid
        insert(copy, after=original)
    for original in rng.sample(valid, share(0.04)):
        copy = Delivery(
            original.path_vendor, "duplicate_resigned", "valid", original.body, 202, "processed", original.event
        )
        insert(copy, after=original)

    # --- rejected: forged or unauthenticated ---------------------------------------
    forgeries = ["tampered", "wrong_secret", "missing_signature", "malformed_signature", "unknown_key_id"]
    for number, source in enumerate(rng.sample(genuine, share(0.03, 10))):
        kind = forgeries[number % len(forgeries)]
        ghost = _ghost(rng, source)
        delivery = Delivery(
            source["vendor"], "invalid_signature", kind, render(ghost, rng.randrange(4)), 401, "rejected", ghost
        )
        if kind == "tampered":
            # A genuine request captured in transit, body altered, original signature kept.
            ghost["event_id"] = source["event_id"]
            delivery.body = render(ghost, 0)
            delivery.sign_body = render(source, 0)
        insert(delivery)

    # --- rejected: authentic but outside the timestamp tolerance -------------------
    for number, source in enumerate(rng.sample(genuine, share(0.02, 6))):
        ghost = _ghost(rng, source)
        kind = "stale" if number % 3 else "future"
        insert(
            Delivery(source["vendor"], "stale_timestamp", kind, render(ghost, rng.randrange(4)), 401, "rejected", ghost)
        )

    # --- accepted, archived, quarantined -------------------------------------------
    for number, source in enumerate(rng.sample(genuine, share(0.01, 6))):
        whole = render(_ghost(rng, source), 0)
        body = [whole[: len(whole) // 2], b"<html><body>502 Bad Gateway</body></html>", b""][number % 3]
        if body == b"":
            body = b" " * (number + 1)  # distinct bodies, still not JSON
        insert(Delivery(source["vendor"], "malformed_json", "valid", body, 202, "quarantine:malformed_json"))

    for source in rng.sample(genuine, share(0.01, 6)):
        ghost = _ghost(rng, source)
        ghost["vendor_type"] = _UNKNOWN_TYPE[source["vendor"]]
        insert(
            Delivery(
                source["vendor"],
                "unknown_event_type",
                "valid",
                render(ghost, rng.randrange(4)),
                202,
                "quarantine:unknown_event_type",
            )
        )

    for number, source in enumerate(rng.sample(genuine, share(0.005, 4))):
        ghost = _ghost(rng, source)
        body = b"[1, 2, %d]" % number if number % 4 == 3 else _drop_required_field(render(ghost, 0), source["vendor"])
        insert(Delivery(source["vendor"], "schema_invalid", "valid", body, 202, "quarantine:schema_invalid"))

    # --- accepted, archived, impossible to process: the poison messages -------------
    for number in range(3):
        payment = _payment(rng, f"LA-9{number:05d}", base_time - timedelta(hours=5 + number))[0]
        payment["details"]["amount_minor"] = POISON_AMOUNT + number
        insert(Delivery("meridianpay", "poison", "valid", render(payment, number), 202, "dlq", payment))

    # --- rejected before verification ----------------------------------------------
    for name in ("acmecollections", "meridianpay2"):
        insert(Delivery(name, "unknown_vendor", "valid", render(rng.choice(genuine), 0), 404, "rejected"))
    oversized = json.dumps({"id": "evt_big", "type": "payment.settled", "padding": "x" * 1_100_000}).encode()
    insert(Delivery("meridianpay", "body_too_large", "valid", oversized, 413, "rejected"))

    for index, delivery in enumerate(plan):
        delivery.index = index
    by_uid = {delivery.uid: delivery.index for delivery in plan}
    for delivery in plan:
        if delivery.reuse_of is not None:
            delivery.reuse_of = by_uid[delivery.reuse_of]
    return plan


def fingerprint(plan: list[Delivery]) -> str:
    """Digest of everything in the plan. Equal seeds and inputs give equal digests."""
    digest = hashlib.sha256()
    for d in plan:
        digest.update(
            f"{d.index}|{d.path_vendor}|{d.category}|{d.signing}|{d.expected_status}|{d.expect}|{d.reuse_of}|".encode()
        )
        digest.update(hashlib.sha256(d.body).digest())
        digest.update(hashlib.sha256(d.sign_body or b"").digest())
    return digest.hexdigest()


def reordered_entities(plan: list[Delivery]) -> int:
    """Entities whose first valid deliveries are not in version order."""
    seen: dict[str, list[int]] = {}
    for d in plan:
        if d.category == "valid" and d.event is not None:
            seen.setdefault(f"{d.event['entity_type']}#{d.event['entity_id']}", []).append(d.event["version"])
    return sum(1 for versions in seen.values() if versions != sorted(versions))
