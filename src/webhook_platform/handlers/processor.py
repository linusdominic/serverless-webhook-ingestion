"""Processor: SQS -> fetch archived body -> normalise -> state -> landing -> ledger.

Order of effects for one event, and why:

1. Conditional state update. Idempotent and monotonic: repeating it, or running it
   after a newer event, changes nothing.
2. Write to the curated landing zone. At-least-once; the compactor deduplicates.
3. Record the event in the idempotency ledger. Last, so that an event is only ever
   marked processed after everything it should cause is durable.

A crash between any two steps leaves the message on the queue. The retry repeats
steps that are safe to repeat. There is no step whose partial completion needs
cleaning up, which is why there is no in-progress marker or lease.

Failures are reported per message (ReportBatchItemFailures), so one bad message does
not make the other nine in its batch reprocess.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.config import Config

from .. import landing, log, store
from ..config import env, env_int
from ..layout import quarantine_key
from ..metrics import Metrics
from ..normalize import CanonicalEvent, PermanentError, normalize
from ..ordering import Decision
from ..pii import Tokenizer
from ..s3util import put_if_absent
from ..secrets import SecretCache, parse_pepper

_BOTO = Config(connect_timeout=2, read_timeout=10, retries={"max_attempts": 4, "mode": "standard"})
_s3 = boto3.client("s3", config=_BOTO)
_dynamodb = boto3.client("dynamodb", config=_BOTO)
_secrets = SecretCache(boto3.client("secretsmanager", config=_BOTO), ttl_seconds=900)


@dataclass(frozen=True)
class Pointer:
    vendor: str
    key: str
    sha256: str
    correlation_id: str
    replay: bool
    ignore_idempotency: bool


@dataclass(frozen=True)
class _Pending:
    message_id: str
    pointer: Pointer
    event: CanonicalEvent
    decision: Decision
    ingested_at: str


def parse_pointer(body: str, raw_bucket: str) -> Pointer:
    doc = json.loads(body)
    if doc.get("schema") != 1:
        raise ValueError("unsupported pointer schema")
    if doc["bucket"] != raw_bucket:
        # A message can only ever point into the archive this function is allowed to read.
        raise ValueError("pointer names a different bucket")
    return Pointer(
        vendor=str(doc["vendor"]),
        key=str(doc["key"]),
        sha256=str(doc["sha256"]),
        correlation_id=str(doc.get("correlation_id") or "unknown"),
        replay=bool(doc.get("replay")),
        ignore_idempotency=bool(doc.get("ignore_idempotency")),
    )


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _fetch(pointer: Pointer) -> bytes:
    body: bytes = _s3.get_object(Bucket=env("RAW_BUCKET"), Key=pointer.key)["Body"].read()
    if hashlib.sha256(body).hexdigest() != pointer.sha256:
        raise RuntimeError("archived object does not match the pointer digest")
    return body


def _quarantine(pointer: Pointer, error: PermanentError) -> None:
    """Record a pointer to a payload that cannot be processed as it stands.

    The payload itself stays in the raw archive, and only there. The key is derived
    from the payload and the write is create-only, so a redelivery or a replay of
    the same payload leaves the first record as it is.
    """
    key = quarantine_key(pointer.vendor, error.reason, pointer.sha256)
    put_if_absent(
        _s3,
        Bucket=env("CURATED_BUCKET"),
        Key=key,
        Body=json.dumps(
            {
                "vendor": pointer.vendor,
                "reason": error.reason,
                "detail": error.detail,
                "archive_bucket": env("RAW_BUCKET"),
                "archive_key": pointer.key,
                "sha256": pointer.sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"),
        ContentType="application/json",
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=env("CURATED_KMS_KEY_ARN"),
    )


def _process_one(record: dict[str, Any], tokens: Tokenizer, metrics: Metrics) -> _Pending | None:
    pointer = parse_pointer(record["body"], env("RAW_BUCKET"))
    body = _fetch(pointer)

    try:
        event = normalize(pointer.vendor, body, tokens)
    except PermanentError as error:
        _quarantine(pointer, error)
        metrics.add("EventsQuarantined", Vendor=pointer.vendor, Reason=error.reason)
        log.warning(
            "quarantined",
            correlation_id=pointer.correlation_id,
            vendor=pointer.vendor,
            reason=error.reason,
            detail=error.detail,
            archive_key=pointer.key,
        )
        return None

    if not pointer.ignore_idempotency and store.already_processed(_dynamodb, env("IDEMPOTENCY_TABLE"), event):
        metrics.add("EventsDuplicate", Vendor=event.vendor)
        log.info(
            "duplicate skipped",
            correlation_id=pointer.correlation_id,
            vendor=event.vendor,
            event_id=event.event_id,
            replay=pointer.replay,
        )
        return None

    ingested_at = _now_iso()
    decision = store.apply_event(_dynamodb, env("STATE_TABLE"), event, now_iso=ingested_at)
    return _Pending(record["messageId"], pointer, event, decision, ingested_at)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    metrics = Metrics()
    failures: list[str] = []
    pending: list[_Pending] = []
    tokens = Tokenizer(parse_pepper(_secrets.get(env("PII_PEPPER_SECRET_ID"))))

    for record in event.get("Records", []):
        try:
            result = _process_one(record, tokens, metrics)
            if result is not None:
                pending.append(result)
        except Exception as exc:
            failures.append(record["messageId"])
            metrics.add("ProcessingErrors")
            log.exception(
                "processing failed",
                exc,
                message_id=record["messageId"],
                receive_count=record.get("attributes", {}).get("ApproximateReceiveCount"),
            )

    if pending:
        try:
            landing.write(
                _s3,
                env("CURATED_BUCKET"),
                env("CURATED_KMS_KEY_ARN"),
                [
                    landing.to_record(
                        p.event,
                        ingested_at=p.ingested_at,
                        archive_key=p.pointer.key,
                        correlation_id=p.pointer.correlation_id,
                    )
                    for p in pending
                ],
            )
        except Exception as exc:
            failures.extend(p.message_id for p in pending)
            metrics.add("ProcessingErrors", len(pending))
            log.exception("landing write failed", exc, messages=len(pending))
            pending = []

    expires_at = int(time.time()) + env_int("IDEMPOTENCY_TTL_DAYS", 30) * 86400
    for item in pending:
        try:
            first = store.mark_processed(
                _dynamodb,
                env("IDEMPOTENCY_TABLE"),
                item.event,
                outcome=item.decision,
                ingested_at=item.ingested_at,
                archive_key=item.pointer.key,
                expires_at=expires_at,
            )
        except Exception as exc:
            failures.append(item.message_id)
            metrics.add("ProcessingErrors")
            log.exception("ledger write failed", exc, message_id=item.message_id)
            continue
        if not first:
            # The ledger already had this event. Either this is a forced replay, or
            # another message carrying the same event (in this batch or in a concurrent
            # invocation) got there first. Its effects were the same ones.
            forced = item.pointer.ignore_idempotency
            metrics.add("EventsReprocessed" if forced else "EventsDuplicate", Vendor=item.event.vendor)
            log.info(
                "reprocessed" if forced else "duplicate finished concurrently",
                correlation_id=item.pointer.correlation_id,
                vendor=item.event.vendor,
                event_id=item.event.event_id,
                outcome=item.decision.value,
            )
            continue
        metrics.add("EventsProcessed", Vendor=item.event.vendor, Outcome=item.decision.value)
        log.info(
            "processed",
            correlation_id=item.pointer.correlation_id,
            vendor=item.event.vendor,
            event_id=item.event.event_id,
            event_type=item.event.event_type,
            state_key=item.event.state_key,
            outcome=item.decision.value,
            replay=item.pointer.replay,
        )

    metrics.flush()
    # De-duplicated, order preserved. Lambda treats an empty list as full success.
    return {"batchItemFailures": [{"itemIdentifier": m} for m in dict.fromkeys(failures)]}
