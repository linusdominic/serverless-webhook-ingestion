"""Move messages from the dead-letter queue back to the ingest queue.

Run it after fixing whatever put them there (docs/runbook.md). A message is sent to
the ingest queue before it is deleted from the DLQ, so an interruption can duplicate
a message but never lose one, and duplicates are harmless here.

On AWS the same thing is one API call (sqs:StartMessageMoveTask) or a button in the
console. This script exists because it also works on the emulator and can filter.
"""

from __future__ import annotations

import argparse
import json

from . import sandbox


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vendor", help="only messages for this vendor")
    parser.add_argument("--max", type=int, default=1000, help="stop after this many")
    parser.add_argument("--dry-run", action="store_true", help="show what is in the DLQ and move nothing")
    args = parser.parse_args()

    out = sandbox.outputs()
    sqs = sandbox.client("sqs")
    moved = skipped = 0
    seen: set[str] = set()
    while moved < args.max:
        batch = sqs.receive_message(
            QueueUrl=out["dlq_url"],
            MaxNumberOfMessages=10,
            VisibilityTimeout=30,
            WaitTimeSeconds=1,
            AttributeNames=["ApproximateReceiveCount"],
        ).get("Messages", [])
        batch = [m for m in batch if m["MessageId"] not in seen]
        if not batch:
            break
        for message in batch:
            seen.add(message["MessageId"])
            pointer = json.loads(message["Body"])
            if args.vendor and pointer.get("vendor") != args.vendor:
                skipped += 1
                continue
            print(f"redrive: {'would move' if args.dry_run else 'moving'} {pointer.get('vendor')} {pointer.get('key')}")
            if args.dry_run:
                continue
            sqs.send_message(QueueUrl=out["queue_url"], MessageBody=message["Body"])
            sqs.delete_message(QueueUrl=out["dlq_url"], ReceiptHandle=message["ReceiptHandle"])
            moved += 1
    print(f"redrive: {moved} moved, {skipped} skipped, {len(seen)} seen")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
