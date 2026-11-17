from __future__ import annotations

import random
from datetime import datetime

from webhook_platform import compaction


def row(
    event_id: str,
    ingested: str = "2026-11-17T01:00:01.000Z",
    occurred: str = "2026-11-17T01:00:00.000Z",
    correlation: str = "c",
) -> dict:
    return compaction.canonical_row(
        {
            "event_id": event_id,
            "event_type": "payment.settled",
            "entity_type": "payment",
            "entity_id": "pay_1",
            "loan_application_id": "LA-1",
            "occurred_at": occurred,
            "version": "2",
            "status": "settled",
            "attributes_json": "{}",
            "ingested_at": ingested,
            "archive_key": "raw/k",
            "correlation_id": correlation,
            "vendor": "ignored: partition column",
        }
    )


def test_canonical_row_types():
    r = row("a")
    assert r["occurred_at"] == datetime(2026, 11, 17, 1, 0, 0) and r["occurred_at"].tzinfo is None
    assert r["version"] == 2 and "vendor" not in r


def test_merge_keeps_one_row_per_event_the_earliest_ingested():
    late, early = row("a", ingested="2026-11-17T02:00:00.000Z", correlation="retry"), row("a", correlation="first")
    merged = compaction.merge([late], [early, row("b")])
    assert [r["event_id"] for r in merged] == ["a", "b"]
    assert merged[0]["correlation_id"] == "first"


def test_merge_is_idempotent():
    """Folding the same landing objects in again (a crash before the manifest write) changes nothing."""
    existing, incoming = [row("a"), row("b")], [row("b"), row("c")]
    once = compaction.merge(existing, incoming)
    assert compaction.merge(once, incoming) == once
    assert compaction.merge(once, []) == once


def test_merge_does_not_depend_on_arrival_order():
    rng = random.Random(3)
    rows = [
        row(f"e{n % 40}", ingested=f"2026-11-17T01:{rng.randint(0, 59):02d}:00.000Z", correlation=f"c{n}")
        for n in range(120)
    ]
    expected = compaction.merge([], rows)
    for _ in range(20):
        rng.shuffle(rows)
        cut = rng.randint(0, len(rows))
        assert compaction.merge(rows[:cut], rows[cut:]) == expected
    assert len(expected) == 40


def test_rows_are_sorted_by_event_time_then_id():
    merged = compaction.merge([], [row("b"), row("a"), row("z", occurred="2026-11-16T00:00:00.000Z")])
    assert [r["event_id"] for r in merged] == ["z", "a", "b"]


def test_parquet_round_trip_and_determinism():
    rows = compaction.merge([], [row("a"), row("b", occurred="2026-11-17T01:00:00.123Z")])
    data = compaction.to_parquet(rows)
    assert data == compaction.to_parquet(rows)
    assert compaction.from_parquet(data) == rows
    assert data[:4] == b"PAR1"


def test_merging_with_rows_read_back_from_parquet():
    stored = compaction.from_parquet(compaction.to_parquet([row("a")]))
    assert compaction.merge(stored, [row("a"), row("b")]) == compaction.merge([], [row("a"), row("b")])
