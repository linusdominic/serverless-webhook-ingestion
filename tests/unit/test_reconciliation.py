from __future__ import annotations

from datetime import datetime, timezone

import pytest

from webhook_platform.reconciliation import ingest_day_key, parse_timestamp, window


def test_window_ends_before_compaction_started_by_the_margin():
    period = window("2026-11-17T12:00:00.000Z", margin_seconds=900, lookback_hours=48)
    assert period.end == datetime(2026, 11, 17, 11, 45, tzinfo=timezone.utc)
    assert period.start == datetime(2026, 11, 15, 11, 45, tzinfo=timezone.utc)
    assert (period.start_iso, period.end_iso) == ("2026-11-15T11:45:00.000Z", "2026-11-17T11:45:00.000Z")


def test_half_open_window_as_a_closed_range_for_dynamodb():
    period = window("2026-11-17T12:00:00.000Z", margin_seconds=0, lookback_hours=1)
    assert period.last_included_iso == "2026-11-17T11:59:59.999Z"
    # ISO strings in one format compare like the times they denote.
    assert period.start_iso <= "2026-11-17T11:00:00.000Z" <= period.last_included_iso
    assert not (period.start_iso <= "2026-11-17T12:00:00.000Z" <= period.last_included_iso)


def test_sql_bounds_are_formatted_not_pasted():
    period = window("2026-11-17T12:00:00.123456+00:00", margin_seconds=0, lookback_hours=1)
    assert period.end_sql == "2026-11-17 12:00:00.123"
    assert period.start_sql == "2026-11-17 11:00:00.123"


@pytest.mark.parametrize(
    ("started", "hours", "days"),
    [
        ("2026-11-17T12:00:00.000Z", 6, ["2026-11-17"]),
        ("2026-11-17T12:00:00.000Z", 48, ["2026-11-15", "2026-11-16", "2026-11-17"]),
        ("2026-11-17T00:00:00.000Z", 24, ["2026-11-16"]),  # ends exactly at midnight: the 17th is not touched
        ("2026-03-01T01:00:00.000Z", 2, ["2026-02-28", "2026-03-01"]),
    ],
)
def test_ingest_days_cover_the_window_and_nothing_more(started, hours, days):
    assert window(started, margin_seconds=0, lookback_hours=hours).ingest_days() == days


def test_offsets_are_normalised_to_utc():
    assert parse_timestamp("2026-11-17T03:00:00+02:00") == datetime(2026, 11, 17, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="no offset"):
        parse_timestamp("2026-11-17T03:00:00")


def test_lookback_must_be_positive():
    with pytest.raises(ValueError, match="at least 1"):
        window("2026-11-17T12:00:00.000Z", margin_seconds=0, lookback_hours=0)


def test_index_key_is_vendor_and_ingest_date():
    assert ingest_day_key("meridianpay", "2026-11-17T01:02:03.456Z") == "meridianpay#2026-11-17"
    period = window("2026-11-17T12:00:00.000Z", margin_seconds=0, lookback_hours=6)
    assert [ingest_day_key("callwright", day) for day in period.ingest_days()] == ["callwright#2026-11-17"]
