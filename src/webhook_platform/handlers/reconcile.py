"""Reconciliation: does the compacted table hold every event the processor finished?

Two independent counts per vendor, both restricted to events ingested in a recent
window that ends when compaction started, minus a margin for work in flight
(see reconciliation.py):

    ledger     items in the idempotency table, written by the processor
    compacted  rows in the Parquet table, counted by Athena

They are produced by different code paths from different stores. Equal counts mean
nothing was lost between "processed" and "queryable". compacted < ledger is data loss
in the curated zone. compacted > ledger is an event that landed but was never marked
processed, which means a message is stuck in retry or sitting in the DLQ after its
landing write.

The ledger is counted with Query on an index keyed by vendor and ingest date, never
with Scan, so a run costs the same whether the table holds a day or a year.
"""

from __future__ import annotations

import json
from typing import Any

import boto3
from botocore.config import Config

from .. import log
from ..athena import run_query
from ..config import env, env_int
from ..metrics import Metrics
from ..reconciliation import Window, ingest_day_key, window
from ..vendors import VENDORS

_BOTO = Config(retries={"max_attempts": 4, "mode": "standard"})
_athena = boto3.client("athena", config=_BOTO)
_dynamodb = boto3.client("dynamodb", config=_BOTO)


class ReconciliationError(Exception):
    pass


def _ledger_count(vendor: str, period: Window) -> int:
    """Ledger records for one vendor in the window, counted from the index.

    One Query per ingest date the window touches. Only index entries inside the
    window are read, so the cost follows the window, not the table.
    """
    total = 0
    for day in period.ingest_days():
        kwargs: dict[str, Any] = {
            "TableName": env("IDEMPOTENCY_TABLE"),
            "IndexName": env("IDEMPOTENCY_INGEST_INDEX"),
            "Select": "COUNT",
            "KeyConditionExpression": "ingest_day = :day AND ingested_at BETWEEN :first AND :last",
            "ExpressionAttributeValues": {
                ":day": {"S": ingest_day_key(vendor, day)},
                ":first": {"S": period.start_iso},
                ":last": {"S": period.last_included_iso},
            },
        }
        while True:
            page = _dynamodb.query(**kwargs)
            total += int(page["Count"])
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return total


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    period = window(
        event["started_at"],
        margin_seconds=env_int("RECONCILE_MARGIN_SECONDS", 900),
        lookback_hours=env_int("RECONCILE_LOOKBACK_HOURS", 48),
    )

    _, rows = run_query(
        _athena,
        # The bounds are formatted from datetimes; no caller-supplied text reaches the query.
        "SELECT vendor, count(*) AS n FROM events "  # noqa: S608
        f"WHERE ingested_at >= TIMESTAMP '{period.start_sql}' AND ingested_at < TIMESTAMP '{period.end_sql}' "
        "GROUP BY vendor",
        workgroup=env("ATHENA_WORKGROUP"),
        database=env("GLUE_DATABASE"),
    )
    compacted = {str(row["vendor"]): int(row["n"] or 0) for row in rows}

    metrics = Metrics()
    vendors = []
    for vendor in sorted(VENDORS):
        ledger = _ledger_count(vendor, period)
        in_table = compacted.get(vendor, 0)
        vendors.append({"vendor": vendor, "ledger": ledger, "compacted": in_table, "difference": in_table - ledger})
        metrics.add("ReconciliationDifference", abs(in_table - ledger), Vendor=vendor)
    metrics.flush()

    result = {
        "window_start": period.start_iso,
        "window_end": period.end_iso,
        "vendors": vendors,
        "matched": all(v["difference"] == 0 for v in vendors),
    }
    log.info("reconciliation", **result)
    if not result["matched"]:
        raise ReconciliationError(json.dumps(result))
    return result
