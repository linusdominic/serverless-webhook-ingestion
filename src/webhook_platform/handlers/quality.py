"""Data quality: run every check in sql/dq through Athena and fail on any violation.

Each check is one SQL file that returns a single row with a ``violations`` count.
Zero passes. The checks stay in SQL so that the people who query the tables can read
and add to them without touching Python.

Every check reads the compacted Parquet table only, and uses SQL that means the same
thing in Athena's engine (Trino) and in DuckDB, which is what the local emulator runs
queries with. docs/adr/0001-emulator.md explains why the JSON landing table is not
queried here.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from importlib import resources
from typing import Any

import boto3
from botocore.config import Config

from .. import log
from ..athena import run_query
from ..config import env
from ..metrics import Metrics

_BOTO = Config(retries={"max_attempts": 4, "mode": "standard"})
_athena = boto3.client("athena", config=_BOTO)
_s3 = boto3.client("s3", config=_BOTO)


class DataQualityError(Exception):
    pass


def load_checks() -> list[tuple[str, str]]:
    directory = resources.files("webhook_platform") / "sql" / "dq"
    checks = [
        (item.name[: -len(".sql")], item.read_text("utf-8"))
        for item in directory.iterdir()
        if item.name.endswith(".sql")
    ]
    return sorted(checks)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    metrics = Metrics()
    results = []
    for name, sql in load_checks():
        execution_id, rows = run_query(_athena, sql, workgroup=env("ATHENA_WORKGROUP"), database=env("GLUE_DATABASE"))
        violations = int(rows[0]["violations"] or 0) if rows else -1
        results.append({"check": name, "violations": violations, "query_execution_id": execution_id})
        metrics.add("DataQualityViolations", max(violations, 0), Check=name)
        log.info("dq check", check=name, violations=violations, query_execution_id=execution_id)

    failed = [r for r in results if r["violations"] != 0]
    now = datetime.now(tz=timezone.utc)
    report_key = f"dq-results/dt={now:%Y-%m-%d}/{now:%H%M%S}-{getattr(context, 'aws_request_id', 'local')}.json"
    _s3.put_object(
        Bucket=env("CURATED_BUCKET"),
        Key=report_key,
        Body=json.dumps({"ran_at": now.isoformat(), "passed": not failed, "checks": results}).encode("utf-8"),
        ContentType="application/json",
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=env("CURATED_KMS_KEY_ARN"),
    )
    metrics.flush()
    if failed:
        raise DataQualityError(json.dumps({"failed": failed, "report": report_key}))
    return {"passed": True, "checks": results, "report": report_key}
