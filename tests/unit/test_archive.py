from __future__ import annotations

from datetime import datetime, timezone

import pytest

from webhook_platform.archive import archive_key, body_sha256, pack_headers, parse_archive_key, unpack_headers

SHA = "a" * 64


def ts(*args: int) -> float:
    return datetime(*args, tzinfo=timezone.utc).timestamp()


def test_key_layout():
    key = archive_key("meridianpay", ts(2026, 11, 17, 9, 30, 5), SHA)
    assert key == f"raw/vendor=meridianpay/dt=2026-11-17/hour=09/{SHA}.body"


def test_partition_is_utc_whatever_the_local_zone(monkeypatch):
    import time

    monkeypatch.setenv("TZ", "Pacific/Auckland")
    time.tzset()
    try:
        assert "/dt=2026-11-17/hour=23/" in archive_key("v", ts(2026, 11, 17, 23, 59, 59), SHA)
    finally:
        monkeypatch.undo()
        time.tzset()


@pytest.mark.parametrize(
    ("when", "expected"),
    [
        (ts(2026, 11, 17, 23, 59, 59), "dt=2026-11-17/hour=23"),
        (ts(2026, 11, 18, 0, 0, 0), "dt=2026-11-18/hour=00"),
        (ts(2026, 12, 31, 23, 59, 59) + 0.999, "dt=2026-12-31/hour=23"),
    ],
)
def test_hour_boundaries(when, expected):
    assert expected in archive_key("v", when, SHA)


def test_same_request_maps_to_the_same_key():
    """A retransmission carries the same signed timestamp and the same bytes."""
    body = b'{"id": 1}'
    first = archive_key("callwright", 1_794_870_000.0, body_sha256(body))
    again = archive_key("callwright", 1_794_870_000.0, body_sha256(body))
    assert first == again


def test_different_bytes_never_share_a_key():
    a = archive_key("v", 1_794_870_000.0, body_sha256(b'{"a":1}'))
    b = archive_key("v", 1_794_870_000.0, body_sha256(b'{"a": 1}'))
    assert a != b


def test_parse_round_trip():
    key = archive_key("veritybureau", ts(2026, 1, 2, 3), SHA)
    assert parse_archive_key(key) == {"vendor": "veritybureau", "dt": "2026-01-02", "hour": "03", "sha256": SHA}


@pytest.mark.parametrize(
    "key", ["raw/x", "landing/vendor=a/dt=b/hour=c/d.body", "raw/vendor=a/dt=b/hour=c/d.json", "raw/a/b/c/d.body"]
)
def test_parse_rejects_other_keys(key):
    with pytest.raises(ValueError, match="not an archive key"):
        parse_archive_key(key)


def test_only_listed_headers_are_kept():
    headers = {
        "x-meridian-signature": "sha256=ab",
        "authorization": "Bearer secret",
        "cookie": "session=1",
        "content-type": "application/json",
    }
    kept = unpack_headers(pack_headers(headers, ("x-meridian-signature", "content-type", "x-absent")))
    assert kept == {"x-meridian-signature": "sha256=ab", "content-type": "application/json"}


def test_packed_headers_are_ascii_and_bounded():
    packed = pack_headers({"sig": "é" * 5000}, ("sig",))
    assert packed.isascii()
    assert len(unpack_headers(packed)["sig"]) == 512
