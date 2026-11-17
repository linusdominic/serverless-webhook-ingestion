"""Object key layout of the curated bucket. Pure, no dependencies.

landing/events/vendor=<v>/dt=<event date>/<batch hash>.jsonl.gz    at-least-once
compacted/events/vendor=<v>/dt=<event date>/part-00000.parquet     one row per event
compacted/_manifests/events/vendor=<v>/dt=<event date>.json        compaction bookkeeping
quarantine/vendor=<v>/reason=<r>/<sha256>.json                     pointers to unprocessable payloads
dq-results/dt=<run date>/<time>-<request id>.json                  data-quality reports
athena-results/                                                    query output
"""

from __future__ import annotations

LANDING_PREFIX = "landing/events"
COMPACTED_PREFIX = "compacted/events"
MANIFEST_PREFIX = "compacted/_manifests/events"
QUARANTINE_PREFIX = "quarantine"


def partition_of(key: str) -> tuple[str, str] | None:
    """(vendor, event date) of a landing or compacted object key, or None."""
    parts = key.split("/")
    if len(parts) != 5 or not parts[2].startswith("vendor=") or not parts[3].startswith("dt="):
        return None
    return parts[2][len("vendor=") :], parts[3][len("dt=") :]


def compacted_key(vendor: str, event_date: str) -> str:
    return f"{COMPACTED_PREFIX}/vendor={vendor}/dt={event_date}/part-00000.parquet"


def manifest_key(vendor: str, event_date: str) -> str:
    return f"{MANIFEST_PREFIX}/vendor={vendor}/dt={event_date}.json"


def quarantine_key(vendor: str, reason: str, sha256_hex: str) -> str:
    return f"{QUARANTINE_PREFIX}/vendor={vendor}/reason={reason}/{sha256_hex}.json"
