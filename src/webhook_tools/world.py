"""Read-only views of the two things the verifier compares.

``Truth``  what the load generator sent and what should have become of it,
           computed from .run/manifest.jsonl alone.
``World``  what is actually in the deployed stack: S3, DynamoDB, SQS, Glue, Athena,
           Step Functions, CloudWatch. Loaded lazily, each part once.
"""

from __future__ import annotations

import base64
import contextlib
import gzip
import hashlib
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cached_property
from typing import Any

import pyarrow.parquet as pq
from boto3.dynamodb.types import TypeDeserializer

from . import sandbox
from .vendorsim import expected_attributes

_deserialize = TypeDeserializer().deserialize


def archive_key(vendor: str, signed_at: float, sha256_hex: str) -> str:
    ts = datetime.fromtimestamp(signed_at, tz=timezone.utc)
    return f"raw/vendor={vendor}/dt={ts:%Y-%m-%d}/hour={ts:%H}/{sha256_hex}.body"


@dataclass(frozen=True)
class Truth:
    deliveries: list[dict[str, Any]]
    pepper: bytes

    @classmethod
    def load(cls) -> Truth:
        lines = (sandbox.RUN_DIR / "manifest.jsonl").read_text().splitlines()
        secret = sandbox.client("secretsmanager").get_secret_value(SecretId=sandbox.outputs()["pii_pepper_secret_name"])
        return cls([json.loads(line) for line in lines], json.loads(secret["SecretString"])["pepper"].encode())

    @cached_property
    def accepted(self) -> list[dict[str, Any]]:
        return [d for d in self.deliveries if d["expected_status"] == 202]

    @cached_property
    def archive(self) -> dict[str, bytes]:
        """Expected archive: key -> exact body."""
        return {
            archive_key(d["path_vendor"], d["sent"]["signed_at"], d["body_sha256"]): base64.b64decode(d["body_b64"])
            for d in self.accepted
        }

    @cached_property
    def rejected_digests(self) -> set[str]:
        accepted = {d["body_sha256"] for d in self.accepted}
        return {d["body_sha256"] for d in self.deliveries if d["expected_status"] != 202} - accepted

    @cached_property
    def events(self) -> dict[tuple[str, str], dict[str, Any]]:
        """Every genuine event that should have been processed, by (vendor, event_id)."""
        return {
            (d["event"]["vendor"], d["event"]["event_id"]): d["event"]
            for d in self.accepted
            if d["expect"] == "processed"
        }

    @cached_property
    def forbidden_event_ids(self) -> set[tuple[str, str]]:
        """Event ids that appeared only in rejected, quarantined or poison deliveries."""
        seen = {
            (d["event"]["vendor"], d["event"]["event_id"])
            for d in self.deliveries
            if d["event"] is not None and d["expect"] != "processed"
        }
        return seen - set(self.events)

    @cached_property
    def state(self) -> dict[str, dict[str, Any]]:
        """Expected state table: for each entity, the event with the greatest
        (version, event_id). Computed here from logical events; no platform code."""
        winners: dict[str, dict[str, Any]] = {}
        for event in self.events.values():
            key = f"{event['entity_type']}#{event['entity_id']}"
            current = winners.get(key)
            if current is None or (current["version"], current["event_id"]) < (event["version"], event["event_id"]):
                winners[key] = event
        return {key: self.canonical(event) for key, event in winners.items()}

    def canonical(self, event: dict[str, Any]) -> dict[str, Any]:
        return {
            "vendor": event["vendor"],
            "last_event_id": event["event_id"],
            "last_event_type": event["vendor_type"],
            "entity_type": event["entity_type"],
            "entity_id": event["entity_id"],
            "loan_application_id": event["loan_application_id"],
            "occurred_at": event["occurred_at"],
            "version": event["version"],
            "status": event["status"],
            "attributes": expected_attributes(event, self.pepper),
        }

    @cached_property
    def quarantine(self) -> set[str]:
        return {
            f"quarantine/vendor={d['path_vendor']}/reason={d['expect'].split(':', 1)[1]}/{d['body_sha256']}.json"
            for d in self.accepted
            if d["expect"].startswith("quarantine:")
        }

    @cached_property
    def poison_keys(self) -> set[str]:
        return {
            archive_key(d["path_vendor"], d["sent"]["signed_at"], d["body_sha256"])
            for d in self.accepted
            if d["expect"] == "dlq"
        }

    @cached_property
    def raw_pii(self) -> dict[str, set[str]]:
        names, births = set(), set()
        for d in self.deliveries:
            details = (d["event"] or {}).get("details", {})
            if "full_name" in details:
                names.add(details["full_name"])
                births.add(details["date_of_birth"])
        return {"names": names, "dates_of_birth": births}


def _plain(value: Any) -> Any:
    """DynamoDB numbers come back as Decimal; compare them as int."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if hasattr(value, "as_tuple"):
        return int(value)
    return value


class World:
    def __init__(self) -> None:
        self.out = sandbox.outputs()
        self.s3 = sandbox.client("s3")
        self.ddb = sandbox.client("dynamodb")
        self.sqs = sandbox.client("sqs")

    # --- S3 -----------------------------------------------------------------------

    def list_keys(self, bucket: str, prefix: str) -> list[str]:
        keys: list[str] = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            keys.extend(item["Key"] for item in page.get("Contents", []))
        return keys

    def get(self, bucket: str, key: str) -> bytes:
        body: bytes = self.s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        return body

    def _fetch_all(self, bucket: str, keys: list[str]) -> dict[str, bytes]:
        with ThreadPoolExecutor(max_workers=16) as pool:
            return dict(zip(keys, pool.map(lambda key: self.get(bucket, key), keys), strict=True))

    @cached_property
    def raw_objects(self) -> dict[str, bytes]:
        return self._fetch_all(self.out["raw_bucket"], self.list_keys(self.out["raw_bucket"], ""))

    @cached_property
    def raw_heads(self) -> dict[str, dict[str, Any]]:
        keys = list(self.raw_objects)
        with ThreadPoolExecutor(max_workers=16) as pool:
            heads = pool.map(lambda key: self.s3.head_object(Bucket=self.out["raw_bucket"], Key=key), keys)
            return dict(zip(keys, heads, strict=True))

    @cached_property
    def raw_versions(self) -> tuple[dict[str, int], int]:
        """Version count per key, and the number of delete markers."""
        counts: dict[str, int] = {}
        markers = 0
        for page in self.s3.get_paginator("list_object_versions").paginate(Bucket=self.out["raw_bucket"]):
            for version in page.get("Versions", []):
                counts[version["Key"]] = counts.get(version["Key"], 0) + 1
            markers += len(page.get("DeleteMarkers", []))
        return counts, markers

    @cached_property
    def quarantine_objects(self) -> dict[str, bytes]:
        return self._fetch_all(self.out["curated_bucket"], self.list_keys(self.out["curated_bucket"], "quarantine/"))

    @cached_property
    def landing_objects(self) -> dict[str, bytes]:
        return self._fetch_all(
            self.out["curated_bucket"], self.list_keys(self.out["curated_bucket"], "landing/events/")
        )

    @cached_property
    def landing_records(self) -> list[tuple[str, dict[str, Any]]]:
        records = []
        for key, body in self.landing_objects.items():
            for line in gzip.decompress(body).splitlines():
                records.append((key, json.loads(line)))
        return records

    @cached_property
    def compacted_objects(self) -> dict[str, bytes]:
        return self._fetch_all(
            self.out["curated_bucket"], self.list_keys(self.out["curated_bucket"], "compacted/events/")
        )

    @cached_property
    def compacted_rows(self) -> list[dict[str, Any]]:
        rows = []
        for key, body in self.compacted_objects.items():
            parts = key.split("/")
            vendor, event_date = parts[2].split("=", 1)[1], parts[3].split("=", 1)[1]
            for row in pq.ParquetFile(io.BytesIO(body)).read().to_pylist():
                rows.append({**row, "vendor": vendor, "dt": event_date})
        return rows

    # --- DynamoDB -----------------------------------------------------------------

    def scan(self, table: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for page in self.ddb.get_paginator("scan").paginate(TableName=table, ConsistentRead=True):
            items.extend({k: _plain(_deserialize(v)) for k, v in item.items()} for item in page["Items"])
        return items

    @cached_property
    def state_items(self) -> dict[str, dict[str, Any]]:
        return {item["pk"]: item for item in self.scan(self.out["state_table"])}

    @cached_property
    def ledger_items(self) -> dict[str, dict[str, Any]]:
        return {item["pk"]: item for item in self.scan(self.out["idempotency_table"])}

    # --- SQS ----------------------------------------------------------------------

    def queue_counts(self, url: str) -> tuple[int, int]:
        attributes = self.sqs.get_queue_attributes(
            QueueUrl=url, AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"]
        )["Attributes"]
        return int(attributes["ApproximateNumberOfMessages"]), int(attributes["ApproximateNumberOfMessagesNotVisible"])

    def peek(self, url: str, *, hide_seconds: int = 15) -> list[dict[str, Any]]:
        """Every message on a queue, without deleting any. Each is hidden briefly so
        the next receive returns different ones; all reappear afterwards."""
        seen: dict[str, dict[str, Any]] = {}
        empty = 0
        while empty < 2:
            batch = self.sqs.receive_message(
                QueueUrl=url, MaxNumberOfMessages=10, VisibilityTimeout=hide_seconds, WaitTimeSeconds=1
            ).get("Messages", [])
            empty = empty + 1 if not batch else 0
            for message in batch:
                seen[message["MessageId"]] = message
        for message in seen.values():  # make them visible again straight away
            with contextlib.suppress(Exception):  # best effort; they reappear on their own
                self.sqs.change_message_visibility(
                    QueueUrl=url, ReceiptHandle=message["ReceiptHandle"], VisibilityTimeout=0
                )
        return list(seen.values())

    @cached_property
    def dlq_messages(self) -> list[dict[str, Any]]:
        return self.peek(self.out["dlq_url"])

    # --- Glue, Athena, Step Functions, CloudWatch ----------------------------------

    @cached_property
    def glue_partitions(self) -> set[tuple[str, str]]:
        glue = sandbox.client("glue")
        found: set[tuple[str, str]] = set()
        pages = glue.get_paginator("get_partitions").paginate(
            DatabaseName=self.out["glue_database"], TableName=self.out["compacted_table"]
        )
        for page in pages:
            found.update((p["Values"][0], p["Values"][1]) for p in page["Partitions"])
        return found

    def athena(self, sql: str) -> list[dict[str, str | None]]:
        athena = sandbox.client("athena")
        execution = athena.start_query_execution(
            QueryString=sql,
            WorkGroup=self.out["athena_workgroup"],
            QueryExecutionContext={"Database": self.out["glue_database"]},
        )["QueryExecutionId"]
        deadline = time.monotonic() + 120
        while True:
            status = athena.get_query_execution(QueryExecutionId=execution)["QueryExecution"]["Status"]
            if status["State"] == "SUCCEEDED":
                break
            if status["State"] in ("FAILED", "CANCELLED") or time.monotonic() > deadline:
                raise RuntimeError(f"athena {status['State']}: {status.get('StateChangeReason', '')[:300]}")
            time.sleep(0.3)
        rows = []
        for page in athena.get_paginator("get_query_results").paginate(QueryExecutionId=execution):
            rows.extend([cell.get("VarCharValue") for cell in row["Data"]] for row in page["ResultSet"]["Rows"])
        header = [str(name) for name in rows[0]]
        return [dict(zip(header, row, strict=True)) for row in rows[1:]]

    def executions(self) -> list[dict[str, Any]]:
        sfn = sandbox.client("stepfunctions")
        found: list[dict[str, Any]] = []
        for page in sfn.get_paginator("list_executions").paginate(stateMachineArn=self.out["state_machine_arn"]):
            found.extend(page["executions"])
        return sorted(found, key=lambda e: e["startDate"], reverse=True)

    def execution_states(self, execution_arn: str) -> list[str]:
        sfn = sandbox.client("stepfunctions")
        states = []
        for page in sfn.get_paginator("get_execution_history").paginate(executionArn=execution_arn):
            states.extend(
                e["stateEnteredEventDetails"]["name"] for e in page["events"] if "stateEnteredEventDetails" in e
            )
        return states

    @cached_property
    def log_text(self) -> dict[str, str]:
        """Every log line of every function, by role."""
        logs = sandbox.client("logs")
        text = {}
        for role, group in self.out["log_group_names"].items():
            lines: list[str] = []
            for page in logs.get_paginator("filter_log_events").paginate(logGroupName=group):
                lines.extend(event["message"] for event in page["events"])
            text[role] = "\n".join(lines)
        return text

    @cached_property
    def secret_values(self) -> list[str]:
        sm = sandbox.client("secretsmanager")
        values = []
        names = [*self.out["signing_secret_names"].values(), self.out["pii_pepper_secret_name"]]
        for name in names:
            doc = json.loads(sm.get_secret_value(SecretId=name)["SecretString"])
            values.extend(key["secret"] for key in doc.get("keys", []))
            if "pepper" in doc:
                values.append(doc["pepper"])
        return values


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
