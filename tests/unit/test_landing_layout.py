from __future__ import annotations

import gzip

from webhook_platform import landing, layout
from webhook_platform.normalize import CanonicalEvent


def event(event_id: str, vendor: str = "meridianpay", occurred_at: str = "2026-11-17T01:00:00.000Z") -> CanonicalEvent:
    return CanonicalEvent(
        vendor, event_id, "payment.settled", "payment", "pay_1", "LA-1", occurred_at, 2, "settled", {"amount_minor": 5}
    )


def record(*args, **kwargs) -> dict:
    return landing.to_record(
        event(*args, **kwargs), ingested_at="2026-11-17T01:00:01.000Z", archive_key="raw/k", correlation_id="c"
    )


def test_records_group_by_vendor_and_event_date():
    records = [
        record("b"),
        record("a"),
        record("c", occurred_at="2026-11-16T23:59:59.999Z"),
        record("d", vendor="callwright"),
    ]
    grouped = landing.group_by_partition(records)
    assert set(grouped) == {("meridianpay", "2026-11-17"), ("meridianpay", "2026-11-16"), ("callwright", "2026-11-17")}
    assert [r["event_id"] for r in grouped[("meridianpay", "2026-11-17")]] == ["a", "b"]


def test_same_event_twice_in_a_batch_is_written_once():
    assert len(landing.group_by_partition([record("a"), record("a")])[("meridianpay", "2026-11-17")]) == 1


def test_object_key_depends_only_on_the_set_of_event_ids():
    first = landing.object_key("meridianpay", "2026-11-17", ["a", "b", "c"])
    assert first == landing.object_key("meridianpay", "2026-11-17", ["c", "a", "b"])
    assert first != landing.object_key("meridianpay", "2026-11-17", ["a", "b"])
    assert first.startswith("landing/events/vendor=meridianpay/dt=2026-11-17/") and first.endswith(".jsonl.gz")
    assert layout.partition_of(first) == ("meridianpay", "2026-11-17")


def test_encoding_is_deterministic_and_round_trips():
    records = [record("a"), record("b")]
    data = landing.encode(records)
    assert data == landing.encode(records)
    assert landing.decode(data) == records
    assert gzip.decompress(data).count(b"\n") == 2


def test_attributes_are_one_json_string_column():
    assert record("a")["attributes_json"] == '{"amount_minor":5}'


def test_write_puts_one_encrypted_object_per_partition():
    class FakeS3:
        def __init__(self):
            self.puts = []

        def put_object(self, **kwargs):
            self.puts.append(kwargs)

    s3 = FakeS3()
    keys = landing.write(s3, "bucket", "arn:aws:kms:key", [record("a"), record("b", vendor="callwright")])
    assert len(keys) == len(s3.puts) == 2
    assert all(p["ServerSideEncryption"] == "aws:kms" and p["SSEKMSKeyId"] == "arn:aws:kms:key" for p in s3.puts)


def test_layout_keys():
    assert layout.compacted_key("v", "2026-11-17") == "compacted/events/vendor=v/dt=2026-11-17/part-00000.parquet"
    assert layout.manifest_key("v", "2026-11-17") == "compacted/_manifests/events/vendor=v/dt=2026-11-17.json"
    assert layout.quarantine_key("v", "malformed_json", "ab") == "quarantine/vendor=v/reason=malformed_json/ab.json"
    assert layout.partition_of("compacted/events/vendor=v/dt=2026-11-17/part-00000.parquet") == ("v", "2026-11-17")
    assert layout.partition_of("landing/events/readme.txt") is None
    # manifests sit outside the table location, so a query engine never reads them
    assert not layout.manifest_key("v", "d").startswith(layout.COMPACTED_PREFIX + "/")
