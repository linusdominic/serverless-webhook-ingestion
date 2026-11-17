"""Catalog: make the Glue Data Catalog match the compacted partitions in S3.

The landing table uses partition projection and needs no registration. The compacted
table uses registered partitions. This function lists the partitions that exist in
S3 and registers any the catalog lacks. It takes no input from the previous step on
purpose: if a run compacts and then fails before this step, the next run still
registers what that run wrote.
"""

from __future__ import annotations

import copy
from typing import Any

import boto3
from botocore.config import Config

from .. import log
from ..config import env
from ..layout import COMPACTED_PREFIX

_BOTO = Config(retries={"max_attempts": 4, "mode": "standard"})
_glue = boto3.client("glue", config=_BOTO)
_s3 = boto3.client("s3", config=_BOTO)
_BATCH = 100  # BatchCreatePartition limit


def _child_prefixes(bucket: str, prefix: str) -> list[str]:
    found: list[str] = []
    paginator = _s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter="/"):
        found.extend(item["Prefix"] for item in page.get("CommonPrefixes", []))
    return found


def partitions_in_s3(bucket: str) -> list[tuple[str, str]]:
    partitions = []
    for vendor_prefix in _child_prefixes(bucket, COMPACTED_PREFIX + "/"):
        vendor_part = vendor_prefix.rstrip("/").rsplit("/", 1)[-1]
        if not vendor_part.startswith("vendor="):
            continue
        for date_prefix in _child_prefixes(bucket, vendor_prefix):
            date_part = date_prefix.rstrip("/").rsplit("/", 1)[-1]
            if date_part.startswith("dt="):
                partitions.append((vendor_part[len("vendor=") :], date_part[len("dt=") :]))
    return sorted(partitions)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    database, table_name = env("GLUE_DATABASE"), env("GLUE_COMPACTED_TABLE")
    wanted = partitions_in_s3(env("CURATED_BUCKET"))

    table = _glue.get_table(DatabaseName=database, Name=table_name)["Table"]
    descriptor = table["StorageDescriptor"]
    location = descriptor["Location"].rstrip("/")

    existing: set[tuple[str, ...]] = set()
    for page in _glue.get_paginator("get_partitions").paginate(DatabaseName=database, TableName=table_name):
        existing.update(tuple(p["Values"]) for p in page["Partitions"])

    missing = [p for p in wanted if p not in existing]
    registered = 0
    for start in range(0, len(missing), _BATCH):
        inputs = []
        for vendor, event_date in missing[start : start + _BATCH]:
            sd = copy.deepcopy(descriptor)
            sd["Location"] = f"{location}/vendor={vendor}/dt={event_date}/"
            inputs.append({"Values": [vendor, event_date], "StorageDescriptor": sd})
        response = _glue.batch_create_partition(DatabaseName=database, TableName=table_name, PartitionInputList=inputs)
        errors = response.get("Errors", [])
        fatal = [e for e in errors if e.get("ErrorDetail", {}).get("ErrorCode") != "AlreadyExistsException"]
        if fatal:
            raise RuntimeError(f"glue rejected {len(fatal)} partitions: {fatal[0].get('ErrorDetail')}")
        registered += len(inputs) - len(errors)

    result = {"partitions_in_s3": len(wanted), "registered": registered, "already_present": len(wanted) - len(missing)}
    log.info("catalog reconciled", **result)
    return result
