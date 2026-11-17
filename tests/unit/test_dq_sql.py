"""The data-quality SQL, run in DuckDB against small tables.

DuckDB is what the local emulator runs Athena queries with, so this is the same
engine the end-to-end run uses. Each check must return zero on clean data and
non-zero on the specific fault it exists to catch.
"""

from __future__ import annotations

from datetime import datetime

import duckdb
import pytest

from webhook_platform.handlers.quality import load_checks

CLEAN = {
    "vendor": "meridianpay",
    "dt": "2026-11-17",
    "event_id": "evt_1",
    "event_type": "payment.settled",
    "entity_type": "payment",
    "entity_id": "pay_1",
    "loan_application_id": "LA-1",
    "occurred_at": datetime(2026, 11, 17, 1, 0, 0),
    "version": 2,
    "status": "settled",
    "attributes_json": '{"amount_minor":5}',
    "ingested_at": datetime(2026, 11, 17, 1, 0, 5),
    "archive_key": "raw/k",
    "correlation_id": "c",
}
CHECKS = dict(load_checks())

FAULTS = {
    "001_event_id_unique": [CLEAN, {**CLEAN, "dt": "2026-11-18"}],
    "002_required_fields_present": [{**CLEAN, "loan_application_id": ""}],
    "003_vendor_entity_type_consistent": [{**CLEAN, "entity_type": "call"}],
    "004_no_raw_pii_fields": [{**CLEAN, "attributes_json": '{"phone":"+14155550123"}'}],
    "005_event_time_not_after_ingest": [{**CLEAN, "occurred_at": datetime(2031, 1, 1)}],
    "006_partition_matches_event_date": [{**CLEAN, "dt": "2026-11-16"}],
    "007_status_in_vendor_vocabulary": [{**CLEAN, "status": "exploded"}],
}


def violations(sql: str, rows: list[dict]) -> int:
    connection = duckdb.connect()
    connection.execute(
        "CREATE TABLE events (vendor VARCHAR, dt VARCHAR, event_id VARCHAR, event_type VARCHAR, entity_type VARCHAR, "
        "entity_id VARCHAR, loan_application_id VARCHAR, occurred_at TIMESTAMP, version BIGINT, status VARCHAR, "
        "attributes_json VARCHAR, ingested_at TIMESTAMP, archive_key VARCHAR, correlation_id VARCHAR)"
    )
    for row in rows:
        connection.execute("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", list(row.values()))
    return int(connection.execute(sql).fetchone()[0])


def test_every_check_has_a_fault_case():
    assert sorted(CHECKS) == sorted(FAULTS)


@pytest.mark.parametrize("name", sorted(CHECKS))
def test_clean_data_passes(name):
    other = {
        **CLEAN,
        "vendor": "callwright",
        "event_id": "u-1",
        "entity_type": "call",
        "status": "completed",
        "attributes_json": '{"to_phone_token":"tok_ab","from_phone_token":"tok_cd"}',
    }
    assert violations(CHECKS[name], [CLEAN, other]) == 0


@pytest.mark.parametrize("name", sorted(CHECKS))
def test_fault_is_caught(name):
    assert violations(CHECKS[name], FAULTS[name]) >= 1


@pytest.mark.parametrize("name", sorted(CHECKS))
def test_check_returns_one_row_named_violations(name):
    connection = duckdb.connect()
    connection.execute(
        "CREATE TABLE events AS SELECT 'a' AS vendor, 'a' AS dt, 'a' AS event_id, 'a' AS entity_type, "
        "'a' AS entity_id, 'a' AS loan_application_id, 'a' AS status, 'a' AS attributes_json, "
        "now()::TIMESTAMP AS occurred_at, now()::TIMESTAMP AS ingested_at LIMIT 0"
    )
    result = connection.execute(CHECKS[name])
    assert [column[0] for column in result.description] == ["violations"] and len(result.fetchall()) == 1
