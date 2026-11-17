"""One-screen status of the deployed stack: queues, stores, pipeline runs, alarms.

Also lists which CloudWatch metric namespaces contain anything, which is the plain
answer to "which alarms can this emulator actually evaluate".
"""

from __future__ import annotations

from collections import Counter
from datetime import timezone

from . import sandbox
from .world import World


def main() -> int:
    world = World()
    out = world.out

    print("queues (visible / in flight)")
    for label, key in (("ingest", "queue_url"), ("dead-letter", "dlq_url"), ("alert sink", "alert_sink_queue_url")):
        visible, in_flight = world.queue_counts(out[key])
        print(f"  {label:<12} {visible:>6} / {in_flight}")

    print("stores")
    for label, table in (("entity state", out["state_table"]), ("idempotency ledger", out["idempotency_table"])):
        print(f"  {label:<20} {world.ddb.scan(TableName=table, Select='COUNT')['Count']:>6} items")
    for label, bucket, prefix in (
        ("raw archive", out["raw_bucket"], "raw/"),
        ("landing", out["curated_bucket"], "landing/"),
        ("compacted Parquet", out["curated_bucket"], "compacted/events/"),
        ("quarantine", out["curated_bucket"], "quarantine/"),
    ):
        print(f"  {label:<20} {len(world.list_keys(bucket, prefix)):>6} objects")

    print("curation pipeline, last five executions")
    for execution in world.executions()[:5]:
        print(f"  {execution['startDate'].astimezone(timezone.utc):%Y-%m-%d %H:%M:%SZ}  {execution['status']}")

    cloudwatch = sandbox.client("cloudwatch")
    print("alarms")
    alarms = []
    for page in cloudwatch.get_paginator("describe_alarms").paginate():
        alarms.extend(page["MetricAlarms"])
    for alarm in sorted(alarms, key=lambda a: a["AlarmName"]):
        namespace = alarm.get("Namespace") or "metric math"
        print(f"  {alarm['StateValue']:<18} {alarm['AlarmName']}  ({namespace})")

    namespaces: Counter[str] = Counter()
    for page in cloudwatch.get_paginator("list_metrics").paginate():
        namespaces.update(metric["Namespace"] for metric in page["Metrics"])
    print("metric namespaces with data")
    for namespace, count in sorted(namespaces.items()):
        print(f"  {namespace:<20} {count} metrics")
    if not namespaces:
        print("  none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
