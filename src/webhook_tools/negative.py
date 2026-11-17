"""Negative test: break the system on purpose and show that the verifier notices.

A verifier that prints PASS proves little until it has been seen to print FAIL.
Each scenario damages one thing the way a bug or an operator mistake would, runs the
invariants that should catch it, checks that they fail, repairs the damage, and
checks that they pass again.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass

from . import sandbox, verify
from .world import Truth, World

Undo = Callable[[], None]


@dataclass
class Scenario:
    name: str
    damage: Callable[[Truth, World], Undo]
    should_fail: list[str]


def _regress_state(truth: Truth, world: World) -> Undo:
    """An older event overwrites newer state: what out-of-order protection prevents."""
    table = world.out["state_table"]
    key = sorted(k for k, v in truth.state.items() if v["status"] == "settled")[0]
    original = world.ddb.get_item(TableName=table, Key={"pk": {"S": key}}, ConsistentRead=True)["Item"]
    world.ddb.update_item(
        TableName=table,
        Key={"pk": {"S": key}},
        UpdateExpression="SET #s = :s, #v = :v",
        ExpressionAttributeNames={"#s": "status", "#v": "version"},
        ExpressionAttributeValues={":s": {"S": "initiated"}, ":v": {"N": "1"}},
    )
    return lambda: world.ddb.put_item(TableName=table, Item=original) and None


def _alter_archive(truth: Truth, world: World) -> Undo:
    """One byte of an archived payload changes."""
    bucket, key = world.out["raw_bucket"], sorted(truth.archive)[0]
    body = bytearray(truth.archive[key])
    body[len(body) // 2] ^= 0x01
    version = world.s3.put_object(Bucket=bucket, Key=key, Body=bytes(body))["VersionId"]
    return lambda: world.s3.delete_object(Bucket=bucket, Key=key, VersionId=version) and None


def _delete_archive(truth: Truth, world: World) -> Undo:
    """An archived payload is deleted."""
    bucket, key = world.out["raw_bucket"], sorted(truth.archive)[1]
    marker = world.s3.delete_object(Bucket=bucket, Key=key)["VersionId"]
    return lambda: world.s3.delete_object(Bucket=bucket, Key=key, VersionId=marker) and None


def _plant_rejected(truth: Truth, world: World) -> Undo:
    """A request that failed signature verification ends up archived anyway."""
    bucket = world.out["raw_bucket"]
    digest = sorted(truth.rejected_digests)[0]
    body = next(d for d in truth.deliveries if d["body_sha256"] == digest)
    import base64

    key = f"raw/vendor={body['path_vendor']}/dt=2026-01-01/hour=00/{digest}.body"
    version = world.s3.put_object(Bucket=bucket, Key=key, Body=base64.b64decode(body["body_b64"]))["VersionId"]
    return lambda: world.s3.delete_object(Bucket=bucket, Key=key, VersionId=version) and None


def _drop_ledger_record(truth: Truth, world: World) -> Undo:
    """A processed event loses its idempotency record."""
    table = world.out["idempotency_table"]
    vendor, event_id = sorted(truth.events)[0]
    key = {"pk": {"S": f"{vendor}#{event_id}"}}
    original = world.ddb.get_item(TableName=table, Key=key, ConsistentRead=True)["Item"]
    world.ddb.delete_item(TableName=table, Key=key)
    return lambda: world.ddb.put_item(TableName=table, Item=original) and None


def _stray_dlq_message(truth: Truth, world: World) -> Undo:
    """A healthy event is dead-lettered."""
    url = world.out["dlq_url"]
    key = sorted(set(truth.archive) - truth.poison_keys)[0]
    marker = json.dumps({"schema": 1, "key": key, "negative_test": True})
    world.sqs.send_message(QueueUrl=url, MessageBody=marker)

    def undo() -> None:
        for _ in range(20):
            for message in world.sqs.receive_message(
                QueueUrl=url, MaxNumberOfMessages=10, VisibilityTimeout=2, WaitTimeSeconds=1
            ).get("Messages", []):
                if message["Body"] == marker:
                    world.sqs.delete_message(QueueUrl=url, ReceiptHandle=message["ReceiptHandle"])
                    return
        raise RuntimeError("could not remove the stray DLQ message")

    return undo


SCENARIOS = [
    Scenario("state regressed to an older event", _regress_state, ["state table equals"]),
    Scenario("archived payload altered by one byte", _alter_archive, ["byte-identical", "exactly once"]),
    Scenario("archived payload deleted", _delete_archive, ["byte-identical", "exactly once"]),
    Scenario("rejected request planted in the archive", _plant_rejected, ["no rejected request", "exactly once"]),
    Scenario("idempotency record removed", _drop_ledger_record, ["idempotency ledger"]),
    Scenario("healthy event sent to the DLQ", _stray_dlq_message, ["DLQ contains exactly"]),
]


def main() -> int:
    baseline = verify.run(sorted({term for scenario in SCENARIOS for term in scenario.should_fail}))
    broken_before = [name for name, (ok, _) in baseline.items() if not ok]
    if broken_before:
        print(f"negative: the system must verify cleanly first; failing now: {broken_before}")
        return 1

    failures = 0
    for number, scenario in enumerate(SCENARIOS, start=1):
        print(f"\n[{number}/{len(SCENARIOS)}] {scenario.name}")
        undo = scenario.damage(Truth.load(), World())
        try:
            damaged = verify.run(scenario.should_fail)
        finally:
            undo()
        repaired = verify.run(scenario.should_fail)

        for name, (ok, detail) in damaged.items():
            print(f"    damaged   {'PASS' if ok else 'FAIL'}  {name}: {detail[:150]}")
        for name, (ok, _) in repaired.items():
            print(f"    repaired  {'PASS' if ok else 'FAIL'}  {name}")
        caught = all(not ok for ok, _ in damaged.values())
        clean = all(ok for ok, _ in repaired.values())
        print(
            f"    => {'caught' if caught else 'NOT CAUGHT'}, "
            f"{'clean after repair' if clean else 'STILL BROKEN AFTER REPAIR'}"
        )
        failures += 0 if caught and clean else 1

    print(f"\nnegative: {len(SCENARIOS) - failures} of {len(SCENARIOS)} corruptions were caught and repaired")
    sandbox.write_json("negative.json", {"scenarios": len(SCENARIOS), "failures": failures})
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
