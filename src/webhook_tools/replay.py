"""Re-drive archived payloads through the processor.

The archive holds only requests that passed signature verification, so a replay
does not go back through the receiver (the signatures are long past their tolerance
window). It lists archive objects for the chosen vendors and hours and enqueues one
pointer message per object, exactly as the receiver would have.

Replay is safe by construction: the processor skips events it has a ledger record
for, and with --ignore-idempotency the state table's ordering condition and the
compactor's deduplication still make every effect a no-op.

    replay --vendor meridianpay --from 2026-11-17T09 --to 2026-11-17T11
    replay --all --assert-unchanged
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from . import sandbox
from .settle import wait_for_drain, wait_for_pipeline
from .world import Truth, World

# Object-level fingerprints. A normal replay must leave these alone too. A forced
# replay (--ignore-idempotency) reprocesses on purpose: it writes new landing objects
# and the compactor rewrites the Parquet files, with the same rows in them.
PHYSICAL = ("landing objects (all versions)", "compacted Parquet (all versions)")


def _hours(start: datetime, end: datetime) -> list[datetime]:
    hours, current = [], start
    while current <= end:
        hours.append(current)
        current += timedelta(hours=1)
    return hours


def select_keys(world: World, vendors: list[str], start: datetime | None, end: datetime | None) -> list[str]:
    bucket = world.out["raw_bucket"]
    keys: list[str] = []
    for vendor in vendors:
        if start is None or end is None:
            keys.extend(world.list_keys(bucket, f"raw/vendor={vendor}/"))
            continue
        for hour in _hours(start, end):
            keys.extend(world.list_keys(bucket, f"raw/vendor={vendor}/dt={hour:%Y-%m-%d}/hour={hour:%H}/"))
    return sorted(keys)


def enqueue(world: World, keys: list[str], *, ignore_idempotency: bool) -> str:
    run_id = f"replay-{uuid.uuid4().hex[:12]}"
    sqs, queue = world.sqs, world.out["queue_url"]
    for start in range(0, len(keys), 10):
        entries = []
        for offset, key in enumerate(keys[start : start + 10]):
            vendor = key.split("/")[1].split("=", 1)[1]
            pointer = {
                "schema": 1,
                "vendor": vendor,
                "bucket": world.out["raw_bucket"],
                "key": key,
                "sha256": key.rsplit("/", 1)[1].removesuffix(".body"),
                "correlation_id": f"{run_id}-{start + offset}",
                "received_at": round(time.time(), 3),
                "replay": True,
                "ignore_idempotency": ignore_idempotency,
            }
            entries.append({"Id": str(offset), "MessageBody": json.dumps(pointer, separators=(",", ":"))})
        response = sqs.send_message_batch(QueueUrl=queue, Entries=entries)
        if response.get("Failed"):
            raise SystemExit(
                f"replay: {len(response['Failed'])} messages were not enqueued; nothing is lost, run again"
            )
    return run_id


def fingerprint(world: World) -> dict[str, str]:
    """Digest of every durable store a replay could possibly touch."""

    def digest(value: Any) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:16]

    def listing(bucket: str, prefix: str) -> list[tuple[str, str, int]]:
        found = []
        for page in world.s3.get_paginator("list_object_versions").paginate(Bucket=bucket, Prefix=prefix):
            found.extend((v["Key"], v["ETag"], v["Size"]) for v in page.get("Versions", []))
        return sorted(found)

    curated, raw = world.out["curated_bucket"], world.out["raw_bucket"]
    rows = sorted(world.compacted_rows, key=lambda r: (r["vendor"], r["event_id"]))
    return {
        "state table": digest(sorted(world.scan(world.out["state_table"]), key=lambda i: i["pk"])),
        "idempotency ledger": digest(sorted(world.scan(world.out["idempotency_table"]), key=lambda i: i["pk"])),
        "raw archive (all versions)": digest(listing(raw, "raw/")),
        "quarantine (all versions)": digest(listing(curated, "quarantine/")),
        "compacted rows": digest(rows),
        PHYSICAL[0]: digest(listing(curated, "landing/")),
        PHYSICAL[1]: digest(listing(curated, "compacted/events/")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vendor", action="append", help="vendor to replay (repeatable)")
    parser.add_argument("--all", action="store_true", help="every vendor")
    parser.add_argument("--from", dest="start", help="first hour, UTC, YYYY-MM-DDTHH")
    parser.add_argument("--to", dest="end", help="last hour, UTC, inclusive, YYYY-MM-DDTHH")
    parser.add_argument("--ignore-idempotency", action="store_true", help="reprocess even events the ledger has seen")
    parser.add_argument(
        "--assert-unchanged", action="store_true", help="fail unless every store is identical afterwards"
    )
    parser.add_argument("--dry-run", action="store_true", help="list what would be replayed")
    args = parser.parse_args()

    world = World()
    vendors = sorted(world.out["signing_secret_names"]) if args.all else (args.vendor or [])
    if not vendors:
        parser.error("give --vendor or --all")
    if bool(args.start) != bool(args.end):
        parser.error("--from and --to go together")
    start = datetime.strptime(args.start, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc) if args.start else None
    end = datetime.strptime(args.end, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc) if args.end else None

    keys = select_keys(world, vendors, start, end)
    window = f"{args.start}..{args.end}" if start else "all time"
    print(f"replay: {len(keys)} archived payloads for {', '.join(vendors)} ({window})")
    if args.dry_run or not keys:
        return 0

    before = fingerprint(world) if args.assert_unchanged else {}
    replays = sandbox.read_json("replays.json")["count"] + 1
    sandbox.write_json("replays.json", {"count": replays})

    started = time.perf_counter()
    run_id = enqueue(world, keys, ignore_idempotency=args.ignore_idempotency)
    print(f"replay: enqueued {len(keys)} pointers as {run_id} in {time.perf_counter() - started:.1f}s")
    if not args.assert_unchanged:
        return 0

    truth = Truth.load()
    drain = wait_for_drain(world, len(truth.poison_keys) * (1 + replays), 900)
    print(f"replay: queue drained after {drain:.1f}s")
    expected_to_change: tuple[str, ...] = ()
    if args.ignore_idempotency:
        # The reprocessed events are in new landing objects; let the scheduled
        # pipeline fold them in before looking at the compacted table.
        pipeline = wait_for_pipeline(world, datetime.now(tz=timezone.utc), 300)
        print(f"replay: scheduled pipeline execution {pipeline['status']} after the replay")
        expected_to_change = PHYSICAL
    after = fingerprint(World())
    changed = [name for name in before if before[name] != after[name] and name not in expected_to_change]
    for name in before:
        if name in expected_to_change:
            verdict = "rewritten (expected)" if before[name] != after[name] else "unchanged"
        else:
            verdict = "CHANGED" if name in changed else "unchanged"
        print(f"replay: {verdict:<20}  {name:<34} {before[name]} -> {after[name]}")
    record = {
        "run_id": run_id,
        "payloads": len(keys),
        "forced": args.ignore_idempotency,
        "drain_seconds": round(drain, 1),
        "before": before,
        "after": after,
    }
    sandbox.write_json("replay-forced.json" if args.ignore_idempotency else "replay.json", record)
    if changed:
        print(f"replay: FAILED, changed: {', '.join(changed)}")
        return 1
    print(f"replay: {len(keys)} payloads reprocessed, nothing changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
