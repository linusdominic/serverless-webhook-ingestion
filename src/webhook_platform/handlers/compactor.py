"""Compactor: fold new landing objects into each partition's Parquet file.

Per (vendor, event date) partition:

    compacted/events/vendor=<v>/dt=<d>/part-00000.parquet     the data
    compacted/_manifests/events/vendor=<v>/dt=<d>.json        landing keys already folded in

A run lists the landing zone, subtracts each partition's manifest, and for every
partition with something new writes merge(existing Parquet, new objects), then the
manifest. The Parquet put replaces the object atomically, so a reader sees the old
file or the new one.

The manifest, not a timestamp watermark, decides what is new: a listing is exact
about keys, while "modified after T" can skip an object whose upload began before T
and finished after the listing.

Crash between the two puts: the next run folds the same objects in again and the
merge discards them as duplicates. Landing objects are never deleted here; a
lifecycle rule expires them.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .. import compaction, landing, log
from ..config import env, env_int
from ..layout import LANDING_PREFIX, compacted_key, manifest_key, partition_of
from ..metrics import Metrics

_BOTO = Config(connect_timeout=3, read_timeout=30, retries={"max_attempts": 4, "mode": "standard"})
_s3 = boto3.client("s3", config=_BOTO)

# Stop starting new partitions when this little time is left; the state machine
# calls again while "more" is true.
_RESERVE_MS = 20_000


def _get_optional(bucket: str, key: str) -> bytes | None:
    try:
        body: bytes = _s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        return body
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return None
        raise


def _put(bucket: str, key: str, body: bytes, content_type: str) -> None:
    _s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType=content_type,
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=env("CURATED_KMS_KEY_ARN"),
    )


def _list_landing(bucket: str) -> dict[tuple[str, str], list[str]]:
    partitions: dict[tuple[str, str], list[str]] = defaultdict(list)
    paginator = _s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=LANDING_PREFIX + "/"):
        for obj in page.get("Contents", []):
            partition = partition_of(obj["Key"])
            if partition is not None:
                partitions[partition].append(obj["Key"])
    return partitions


def _compact_partition(bucket: str, vendor: str, event_date: str, landing_keys: list[str]) -> dict[str, Any] | None:
    raw_manifest = _get_optional(bucket, manifest_key(vendor, event_date))
    included: set[str] = set(json.loads(raw_manifest)["landing_keys"]) if raw_manifest else set()
    new_keys = sorted(set(landing_keys) - included)
    if not new_keys:
        return None

    existing_bytes = _get_optional(bucket, compacted_key(vendor, event_date))
    existing = compaction.from_parquet(existing_bytes) if existing_bytes else []
    incoming: list[dict[str, Any]] = []
    for key in new_keys:
        body = _s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        incoming.extend(compaction.canonical_row(record) for record in landing.decode(body))

    rows = compaction.merge(existing, incoming)
    _put(bucket, compacted_key(vendor, event_date), compaction.to_parquet(rows), "application/vnd.apache.parquet")
    _put(
        bucket,
        manifest_key(vendor, event_date),
        json.dumps({"landing_keys": sorted(included | set(new_keys)), "rows": len(rows)}).encode("utf-8"),
        "application/json",
    )
    return {
        "vendor": vendor,
        "dt": event_date,
        "rows": len(rows),
        "rows_before": len(existing),
        "landing_objects_folded": len(new_keys),
    }


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    started_at = datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    bucket = env("CURATED_BUCKET")
    max_partitions = env_int("MAX_PARTITIONS_PER_INVOCATION", 200)
    metrics = Metrics()

    partitions = _list_landing(bucket)
    rewritten: list[dict[str, Any]] = []
    more = False
    for (vendor, event_date), keys in sorted(partitions.items()):
        out_of_time = context.get_remaining_time_in_millis() < _RESERVE_MS
        if out_of_time or len(rewritten) >= max_partitions:
            more = True
            break
        result = _compact_partition(bucket, vendor, event_date, keys)
        if result is not None:
            rewritten.append(result)
            metrics.add("PartitionsCompacted", Vendor=vendor)
            metrics.add("LandingObjectsCompacted", result["landing_objects_folded"], Vendor=vendor)
            log.info("partition compacted", **result)

    metrics.flush()
    log.info("compaction finished", partitions_seen=len(partitions), partitions_rewritten=len(rewritten), more=more)
    return {
        # On a looped call the first invocation's start time is kept: reconciliation
        # uses it as the cutoff for "should already be in the compacted table".
        "started_at": event.get("started_at") or started_at,
        "partitions_total": len(partitions),
        "partitions_rewritten": len(rewritten),
        "rewritten": rewritten,
        "more": more,
    }
