"""Integration tests against the deployed emulator stack (make up deploy).

Each test sends its own uniquely named events through the real HTTP API and reads
the result from S3 and DynamoDB. They leave those events behind, so `make verify`
afterwards reports them as objects the load generator did not send. Run these last,
or on a stack you are about to tear down.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Any

import pytest
import requests

from webhook_tools import sandbox
from webhook_tools.loadgen import load_keys
from webhook_tools.vendorsim import sign
from webhook_tools.world import World, archive_key

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def world() -> World:
    return World()


@pytest.fixture(scope="module")
def keys() -> dict[str, list[Any]]:
    return load_keys()


def payment(event_id: str, payment_id: str, sequence: int, kind: str) -> bytes:
    doc = {
        "id": event_id,
        "type": f"payment.{kind}",
        "created": int(time.time()) - 30,
        "sequence": sequence,
        "data": {
            "payment_id": payment_id,
            "loan_ref": "LA-IT0001",
            "amount_minor": 4200,
            "currency": "USD",
            "method": "ach",
            "failure_code": None,
        },
    }
    return json.dumps(doc).encode()


def post(body: bytes, headers: dict[str, str]) -> requests.Response:
    return requests.post(
        sandbox.webhook_url("meridianpay"),
        data=body,
        headers={**headers, "Content-Type": "application/json"},
        timeout=30,
    )


def wait_for(probe, timeout: float = 60.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = probe()
        if value:
            return value
        time.sleep(0.5)
    raise AssertionError("condition not met in time")


def state_item(world: World, payment_id: str) -> dict[str, Any] | None:
    return world.ddb.get_item(
        TableName=world.out["state_table"], Key={"pk": {"S": f"payment#{payment_id}"}}, ConsistentRead=True
    ).get("Item")


def ledger_has(world: World, event_id: str) -> bool:
    key = {"pk": {"S": f"meridianpay#{event_id}"}}
    return "Item" in world.ddb.get_item(TableName=world.out["idempotency_table"], Key=key, ConsistentRead=True)


def test_unsigned_request_is_rejected_and_not_archived(world):
    body = payment(f"evt_it_{uuid.uuid4().hex}", "pay_it_unsigned", 1, "initiated")
    response = post(body, {})
    assert response.status_code == 401
    assert response.json() == {"error": "signature_verification_failed"}
    digest = hashlib.sha256(body).hexdigest()
    assert not [key for key in world.list_keys(world.out["raw_bucket"], "raw/vendor=meridianpay/") if digest in key]


def test_signed_event_is_archived_processed_and_reflected_in_state(world, keys):
    suffix = uuid.uuid4().hex[:12]
    event_id, payment_id = f"evt_it_{suffix}", f"pay_it_{suffix}"
    body, now = payment(event_id, payment_id, 2, "settled"), time.time()

    response = post(body, sign("meridianpay", body, keys["meridianpay"][0], now))
    assert response.status_code == 202
    assert response.json()["duplicate"] is False

    key = archive_key("meridianpay", float(int(now)), hashlib.sha256(body).hexdigest())
    assert world.get(world.out["raw_bucket"], key) == body

    wait_for(lambda: ledger_has(world, event_id))
    item = state_item(world, payment_id)
    assert item is not None
    assert (item["status"]["S"], item["version"]["N"], item["last_event_id"]["S"]) == ("settled", "2", event_id)


def test_redelivery_is_one_object_and_one_ledger_record(world, keys):
    suffix = uuid.uuid4().hex[:12]
    event_id, payment_id = f"evt_it_{suffix}", f"pay_it_{suffix}"
    body, now = payment(event_id, payment_id, 1, "initiated"), time.time()
    headers = sign("meridianpay", body, keys["meridianpay"][0], now)

    first, second = post(body, headers), post(body, headers)
    assert (first.status_code, second.status_code) == (202, 202)
    assert second.json()["duplicate"] is True

    key = archive_key("meridianpay", float(int(now)), hashlib.sha256(body).hexdigest())
    versions = world.s3.list_object_versions(Bucket=world.out["raw_bucket"], Prefix=key).get("Versions", [])
    assert len(versions) == 1
    wait_for(lambda: ledger_has(world, event_id))


def test_older_event_arriving_later_does_not_overwrite_state(world, keys):
    suffix = uuid.uuid4().hex[:12]
    payment_id, key = f"pay_it_{suffix}", keys["meridianpay"][0]
    newer = payment(f"evt_it_{suffix}_2", payment_id, 2, "settled")
    older = payment(f"evt_it_{suffix}_1", payment_id, 1, "initiated")

    assert post(newer, sign("meridianpay", newer, key, time.time())).status_code == 202
    wait_for(lambda: ledger_has(world, f"evt_it_{suffix}_2"))
    assert post(older, sign("meridianpay", older, key, time.time())).status_code == 202
    wait_for(lambda: ledger_has(world, f"evt_it_{suffix}_1"))

    item = state_item(world, payment_id)
    assert item is not None
    assert (item["status"]["S"], item["version"]["N"]) == ("settled", "2")


def test_previous_signing_key_is_still_accepted(world, keys):
    body = payment(f"evt_it_{uuid.uuid4().hex[:12]}", f"pay_it_{uuid.uuid4().hex[:12]}", 1, "initiated")
    assert post(body, sign("meridianpay", body, keys["meridianpay"][1], time.time())).status_code == 202
