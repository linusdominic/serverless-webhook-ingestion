"""Run one Athena query and wait for it."""

from __future__ import annotations

import time
from typing import Any


class QueryFailed(RuntimeError):
    pass


def run_query(
    client: Any, sql: str, *, workgroup: str, database: str, timeout_seconds: float = 120.0
) -> tuple[str, list[dict[str, str | None]]]:
    """Returns (query execution id, rows as dicts keyed by column name)."""
    execution_id = client.start_query_execution(
        QueryString=sql,
        WorkGroup=workgroup,
        QueryExecutionContext={"Database": database},
    )["QueryExecutionId"]

    deadline = time.monotonic() + timeout_seconds
    delay = 0.2
    while True:
        status = client.get_query_execution(QueryExecutionId=execution_id)["QueryExecution"]["Status"]
        if status["State"] == "SUCCEEDED":
            break
        if status["State"] in ("FAILED", "CANCELLED"):
            raise QueryFailed(f"{execution_id} {status['State']}: {status.get('StateChangeReason', '')[:500]}")
        if time.monotonic() > deadline:
            client.stop_query_execution(QueryExecutionId=execution_id)
            raise QueryFailed(f"{execution_id} timed out after {timeout_seconds}s")
        time.sleep(delay)
        delay = min(delay * 1.5, 2.0)

    rows: list[list[str | None]] = []
    for page in client.get_paginator("get_query_results").paginate(QueryExecutionId=execution_id):
        rows.extend([cell.get("VarCharValue") for cell in row["Data"]] for row in page["ResultSet"]["Rows"])
    if not rows:
        return execution_id, []
    header = [str(name) for name in rows[0]]
    return execution_id, [dict(zip(header, row, strict=True)) for row in rows[1:]]
