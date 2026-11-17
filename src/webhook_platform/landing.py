"""Curated landing zone: gzip JSON lines, one object per (vendor, event date) per batch.

Key: landing/events/vendor=<v>/dt=<event date>/<hash of the event ids>.jsonl.gz

The partition is the event's own date, so every copy of an event lands in the same
partition whenever it is processed, and compaction can deduplicate one partition at
a time. The object name is a hash of the event ids it holds: a retry of the same
batch overwrites its own object instead of adding another.

Delivery here is at-least-once. Exactly-once is the compactor's job.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
from collections import defaultdict
from typing import Any

from .layout import LANDING_PREFIX
from .normalize import CanonicalEvent


def to_record(event: CanonicalEvent, *, ingested_at: str, archive_key: str, correlation_id: str) -> dict[str, Any]:
    return {
        "vendor": event.vendor,
        "event_id": event.event_id,
        "event_type": event.event_type,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "loan_application_id": event.loan_application_id,
        "occurred_at": event.occurred_at,
        "version": event.version,
        "status": event.status,
        "attributes_json": json.dumps(event.attributes, sort_keys=True, separators=(",", ":")),
        "ingested_at": ingested_at,
        "archive_key": archive_key,
        "correlation_id": correlation_id,
    }


def group_by_partition(records: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Group by (vendor, event date); one record per event id within a partition."""
    grouped: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    for record in records:
        partition = (record["vendor"], record["occurred_at"][:10])
        grouped[partition].setdefault(record["event_id"], record)
    return {partition: [by_id[k] for k in sorted(by_id)] for partition, by_id in grouped.items()}


def object_key(vendor: str, event_date: str, event_ids: list[str]) -> str:
    digest = hashlib.sha256("\n".join(sorted(event_ids)).encode("utf-8")).hexdigest()[:32]
    return f"{LANDING_PREFIX}/vendor={vendor}/dt={event_date}/{digest}.jsonl.gz"


def encode(records: list[dict[str, Any]]) -> bytes:
    buffer = io.BytesIO()
    # mtime=0 keeps the bytes a function of the records alone.
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8"))
            handle.write(b"\n")
    return buffer.getvalue()


def decode(data: bytes) -> list[dict[str, Any]]:
    return [json.loads(line) for line in gzip.decompress(data).splitlines() if line.strip()]


def write(s3: Any, bucket: str, kms_key_arn: str, records: list[dict[str, Any]]) -> list[str]:
    keys = []
    for (vendor, event_date), group in sorted(group_by_partition(records).items()):
        key = object_key(vendor, event_date, [r["event_id"] for r in group])
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=encode(group),
            ContentType="application/x-ndjson",
            ContentEncoding="gzip",
            ServerSideEncryption="aws:kms",
            SSEKMSKeyId=kms_key_arn,
        )
        keys.append(key)
    return keys
