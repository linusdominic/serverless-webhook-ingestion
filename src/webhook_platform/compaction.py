"""Compaction: many small gzip JSON objects -> one deduplicated Parquet file per partition.

Pure except for the pyarrow dependency. The handler supplies bytes and stores bytes.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

SCHEMA = pa.schema(
    [
        pa.field("event_id", pa.string(), nullable=False),
        pa.field("event_type", pa.string(), nullable=False),
        pa.field("entity_type", pa.string(), nullable=False),
        pa.field("entity_id", pa.string(), nullable=False),
        pa.field("loan_application_id", pa.string(), nullable=False),
        pa.field("occurred_at", pa.timestamp("ms"), nullable=False),
        pa.field("version", pa.int64(), nullable=False),
        pa.field("status", pa.string(), nullable=False),
        pa.field("attributes_json", pa.string(), nullable=False),
        pa.field("ingested_at", pa.timestamp("ms"), nullable=False),
        pa.field("archive_key", pa.string(), nullable=False),
        pa.field("correlation_id", pa.string(), nullable=False),
    ]
)
_COLUMNS = [f.name for f in SCHEMA]
_TIMESTAMPS = ("occurred_at", "ingested_at")


def _to_naive_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is None else value.astimezone(timezone.utc).replace(tzinfo=None)
    text = str(value)
    parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def canonical_row(record: dict[str, Any]) -> dict[str, Any]:
    row = {name: record[name] for name in _COLUMNS}
    for name in _TIMESTAMPS:
        row[name] = _to_naive_utc(row[name])
    row["version"] = int(row["version"])
    return row


def merge(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Union, one row per event id, deterministic.

    When the same event landed more than once (a retry after a crash, a forced
    replay), the copy that was ingested first is kept. The rule only has to be
    stable; the copies differ in nothing but bookkeeping columns.
    """
    best: dict[str, dict[str, Any]] = {}
    for row in existing + incoming:
        current = best.get(row["event_id"])
        candidate = (row["ingested_at"], row["archive_key"], row["correlation_id"])
        if current is None or candidate < (current["ingested_at"], current["archive_key"], current["correlation_id"]):
            best[row["event_id"]] = row
    return sorted(best.values(), key=lambda r: (r["occurred_at"], r["event_id"]))


def to_parquet(rows: list[dict[str, Any]]) -> bytes:
    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    buffer = io.BytesIO()
    pq.write_table(table, buffer, compression="zstd")
    return buffer.getvalue()


def from_parquet(data: bytes) -> list[dict[str, Any]]:
    # ParquetFile, not read_table: read_table goes through pyarrow.dataset, which the
    # package does not need for a single in-memory file.
    rows: list[dict[str, Any]] = pq.ParquetFile(io.BytesIO(data)).read().to_pylist()
    return rows
