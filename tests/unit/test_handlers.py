"""Receiver and processor handlers against moto.

moto earns its place here: the properties under test are about the order of side
effects across S3, SQS and DynamoDB (what exists when a step fails), which stubs
would only restate. Timing, IAM and the event source mapping are not covered; the
emulator run covers those.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import importlib
import json
import time
from types import SimpleNamespace
from typing import Any

import boto3
import pytest
from moto import mock_aws

from webhook_tools.plan import POISON_AMOUNT
from webhook_tools.vendorsim import Key, sign

RAW, CURATED = "raw-bucket", "curated-bucket"
CURRENT, PREVIOUS = Key("k-new", b"new-secret"), Key("k-old", b"old-secret")
CONTEXT = SimpleNamespace(aws_request_id="req-1", get_remaining_time_in_millis=lambda: 60_000)


@pytest.fixture
def stack(monkeypatch):
    for name in ("AWS_ENDPOINT_URL", "AWS_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    environment = {
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
        "AWS_DEFAULT_REGION": "us-east-1",
        "RAW_BUCKET": RAW,
        "CURATED_BUCKET": CURATED,
        "RAW_KMS_KEY_ARN": "alias/aws/s3",
        "CURATED_KMS_KEY_ARN": "alias/aws/s3",
        "SIGNING_SECRET_PREFIX": "test/vendors/",
        "PII_PEPPER_SECRET_ID": "test/pepper",
        "STATE_TABLE": "state",
        "IDEMPOTENCY_TABLE": "ledger",
        "MAX_BODY_BYTES": "4096",
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    with mock_aws():
        s3, sqs = boto3.client("s3"), boto3.client("sqs")
        ddb, sm = boto3.client("dynamodb"), boto3.client("secretsmanager")
        for bucket in (RAW, CURATED):
            s3.create_bucket(Bucket=bucket)
            s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
        queue = sqs.create_queue(QueueName="ingest")["QueueUrl"]
        monkeypatch.setenv("QUEUE_URL", queue)
        for table in ("state", "ledger"):
            ddb.create_table(
                TableName=table,
                BillingMode="PAY_PER_REQUEST",
                AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
                KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            )
        keys = {"keys": [{"id": k.key_id, "secret": k.secret.decode()} for k in (CURRENT, PREVIOUS)]}
        for vendor in ("meridianpay", "callwright", "veritybureau"):
            sm.create_secret(Name=f"test/vendors/{vendor}", SecretString=json.dumps(keys))
        sm.create_secret(Name="test/pepper", SecretString=json.dumps({"pepper": "p" * 40}))

        import webhook_platform.handlers.processor as processor
        import webhook_platform.handlers.receiver as receiver

        yield SimpleNamespace(
            receiver=importlib.reload(receiver),
            processor=importlib.reload(processor),
            s3=s3,
            sqs=sqs,
            ddb=ddb,
            sm=sm,
            queue=queue,
        )


# --- helpers ----------------------------------------------------------------------


def payment(
    event_id: str = "evt_1", sequence: int = 2, kind: str = "settled", payment_id: str = "pay_1", amount: int = 125000
) -> bytes:
    doc = {
        "id": event_id,
        "type": f"payment.{kind}",
        "created": int(time.time()) - 60,
        "sequence": sequence,
        "data": {
            "payment_id": payment_id,
            "loan_ref": "LA-000001",
            "amount_minor": amount,
            "currency": "USD",
            "method": "ach",
            "failure_code": None,
        },
    }
    return json.dumps(doc).encode()


def request(
    body: bytes,
    vendor: str = "meridianpay",
    *,
    key: Key = CURRENT,
    at: float | None = None,
    headers: dict | None = None,
    b64: bool = False,
) -> dict:
    signed = sign(vendor, body, key, time.time() if at is None else at) if headers is None else headers
    return {
        "version": "2.0",
        "routeKey": "POST /webhooks/{vendor}",
        "rawPath": f"/webhooks/{vendor}",
        "pathParameters": {"vendor": vendor},
        "headers": {**{k.lower(): v for k, v in signed.items()}, "content-type": "application/json"},
        "requestContext": {"requestId": "corr-1"},
        "body": base64.b64encode(body).decode() if b64 else body.decode(),
        "isBase64Encoded": b64,
    }


def raw_keys(stack) -> list[str]:
    return [o["Key"] for o in stack.s3.list_objects_v2(Bucket=RAW).get("Contents", [])]


def curated_keys(stack, prefix: str) -> list[str]:
    return [o["Key"] for o in stack.s3.list_objects_v2(Bucket=CURATED, Prefix=prefix).get("Contents", [])]


def queued(stack) -> list[dict]:
    messages = stack.sqs.receive_message(QueueUrl=stack.queue, MaxNumberOfMessages=10, VisibilityTimeout=0).get(
        "Messages", []
    )
    return [json.loads(m["Body"]) for m in messages]


def archive(stack, body: bytes, vendor: str = "meridianpay") -> dict[str, Any]:
    """Put a body in the raw bucket and return the SQS record the processor would get."""
    digest = hashlib.sha256(body).hexdigest()
    key = f"raw/vendor={vendor}/dt=2026-11-17/hour=01/{digest}.body"
    stack.s3.put_object(Bucket=RAW, Key=key, Body=body)
    pointer = {"schema": 1, "vendor": vendor, "bucket": RAW, "key": key, "sha256": digest, "correlation_id": "corr-1"}
    return {"messageId": f"m-{digest[:8]}", "body": json.dumps(pointer), "attributes": {"ApproximateReceiveCount": "1"}}


def process(stack, *records: dict) -> list[str]:
    result = stack.processor.handler({"Records": list(records)}, CONTEXT)
    return [failure["itemIdentifier"] for failure in result["batchItemFailures"]]


def state(stack, key: str = "payment#pay_1") -> dict | None:
    return stack.ddb.get_item(TableName="state", Key={"pk": {"S": key}}).get("Item")


def ledger(stack) -> dict[str, str]:
    return {i["pk"]["S"]: i["outcome"]["S"] for i in stack.ddb.scan(TableName="ledger")["Items"]}


def landed(stack) -> list[dict]:
    records = []
    for key in curated_keys(stack, "landing/"):
        body = stack.s3.get_object(Bucket=CURATED, Key=key)["Body"].read()
        records += [json.loads(line) for line in gzip.decompress(body).splitlines()]
    return records


# --- receiver ---------------------------------------------------------------------


def test_accepted_request_is_archived_byte_for_byte_then_enqueued(stack):
    body = b'{ "id" : "evt_1",\n\t"note": "caf\xc3\xa9" }\r\n'
    response = stack.receiver.handler(request(body), CONTEXT)
    assert response["statusCode"] == 202 and json.loads(response["body"])["duplicate"] is False

    [key] = raw_keys(stack)
    stored = stack.s3.get_object(Bucket=RAW, Key=key)
    assert stored["Body"].read() == body
    assert key.endswith(f"/{hashlib.sha256(body).hexdigest()}.body") and key.startswith("raw/vendor=meridianpay/dt=")
    assert stored["Metadata"]["sha256"] == hashlib.sha256(body).hexdigest() and stored["Metadata"]["key-id"] == "k-new"
    kept = json.loads(base64.b64decode(stored["Metadata"]["signature-headers"]))
    assert set(kept) == {"x-meridian-signature", "x-meridian-timestamp", "content-type"}

    [pointer] = queued(stack)
    assert pointer["key"] == key and pointer["bucket"] == RAW and pointer["correlation_id"] == "corr-1"
    assert "body" not in pointer  # a pointer, never the payload


def test_binary_body_arrives_base64_encoded_and_is_archived_exactly(stack):
    body = bytes(range(256))
    assert stack.receiver.handler(request(body, b64=True), CONTEXT)["statusCode"] == 202
    assert stack.s3.get_object(Bucket=RAW, Key=raw_keys(stack)[0])["Body"].read() == body


def test_redelivery_does_not_rewrite_the_archive_but_is_enqueued_again(stack):
    event = request(payment())
    first, second = stack.receiver.handler(event, CONTEXT), stack.receiver.handler(event, CONTEXT)
    assert (first["statusCode"], second["statusCode"]) == (202, 202)
    assert json.loads(second["body"])["duplicate"] is True
    versions = stack.s3.list_object_versions(Bucket=RAW)["Versions"]
    assert len(versions) == 1
    assert len(queued(stack)) == 2


@pytest.mark.parametrize(
    "event",
    [
        lambda: request(payment(), key=Key("k-new", b"wrong")),
        lambda: request(payment(), at=time.time() - 3600),
        lambda: request(payment(), at=time.time() + 3600),
        lambda: request(payment(), headers={}),
        lambda: {**request(payment()), "body": payment(amount=1).decode()},
    ],
    ids=["wrong-secret", "stale", "future", "unsigned", "tampered"],
)
def test_rejected_request_leaves_no_trace(stack, event):
    response = stack.receiver.handler(event(), CONTEXT)
    assert response["statusCode"] == 401
    assert json.loads(response["body"]) == {"error": "signature_verification_failed"}  # one answer for every reason
    assert raw_keys(stack) == [] and queued(stack) == []


def test_unknown_vendor_and_oversized_body(stack):
    assert stack.receiver.handler(request(payment(), vendor="acme", headers={}), CONTEXT)["statusCode"] == 404
    assert stack.receiver.handler(request(b"x" * 5000), CONTEXT)["statusCode"] == 413
    assert raw_keys(stack) == [] and queued(stack) == []


def test_previous_key_still_verifies_during_rotation(stack):
    assert stack.receiver.handler(request(payment(), key=PREVIOUS), CONTEXT)["statusCode"] == 202


def test_rotated_secret_is_picked_up_without_a_deploy(stack):
    rotated = Key("k-2027", b"brand-new-secret")
    assert stack.receiver.handler(request(payment("evt_a")), CONTEXT)["statusCode"] == 202  # warms the cache
    stack.sm.put_secret_value(
        SecretId="test/vendors/meridianpay",
        SecretString=json.dumps({"keys": [{"id": rotated.key_id, "secret": rotated.secret.decode()}]}),
    )
    # Within the minimum refresh interval the stale cache is used and the request fails...
    assert stack.receiver.handler(request(payment("evt_b"), key=rotated), CONTEXT)["statusCode"] == 401
    # ...and once it has passed, a failed verification triggers one re-read.
    for entry in stack.receiver._secrets._entries.values():
        entry.fetched_at -= 60
    assert stack.receiver.handler(request(payment("evt_b"), key=rotated), CONTEXT)["statusCode"] == 202


def test_archive_failure_is_a_5xx_and_nothing_is_enqueued(stack, monkeypatch):
    def broken(**_: Any) -> None:
        raise RuntimeError("S3 is down")

    monkeypatch.setattr(stack.receiver._s3, "put_object", broken)
    assert stack.receiver.handler(request(payment()), CONTEXT)["statusCode"] == 500
    assert queued(stack) == []


def test_enqueue_failure_is_a_5xx_and_the_retry_completes_it(stack, monkeypatch):
    event = request(payment())
    with monkeypatch.context() as patch:
        patch.setattr(
            stack.receiver._sqs, "send_message", lambda **_: (_ for _ in ()).throw(RuntimeError("SQS is down"))
        )
        assert stack.receiver.handler(event, CONTEXT)["statusCode"] == 500
    assert len(raw_keys(stack)) == 1 and queued(stack) == []  # archived, not acknowledged

    retry = stack.receiver.handler(event, CONTEXT)  # the vendor retries the 5xx
    assert retry["statusCode"] == 202 and json.loads(retry["body"])["duplicate"] is True
    assert len(queued(stack)) == 1 and len(stack.s3.list_object_versions(Bucket=RAW)["Versions"]) == 1


# --- processor --------------------------------------------------------------------


def test_event_updates_state_lands_and_is_recorded(stack):
    assert process(stack, archive(stack, payment())) == []
    item = state(stack)
    assert item["status"]["S"] == "settled" and item["version"]["N"] == "2" and item["last_event_id"]["S"] == "evt_1"
    assert ledger(stack) == {"meridianpay#evt_1": "applied"}
    [record] = landed(stack)
    assert record["event_id"] == "evt_1" and json.loads(record["attributes_json"])["amount_minor"] == 125000


def test_older_event_never_overwrites_newer_state(stack):
    process(stack, archive(stack, payment("evt_2", sequence=2, kind="settled")))
    process(stack, archive(stack, payment("evt_1", sequence=1, kind="initiated")))
    assert state(stack)["status"]["S"] == "settled" and state(stack)["version"]["N"] == "2"
    assert ledger(stack) == {"meridianpay#evt_2": "applied", "meridianpay#evt_1": "stale"}
    assert {r["event_id"] for r in landed(stack)} == {"evt_1", "evt_2"}  # the stale event still happened


def test_redelivered_event_is_skipped(stack):
    record = archive(stack, payment())
    process(stack, record)
    before = (state(stack), curated_keys(stack, "landing/"))
    assert process(stack, record) == []
    assert (state(stack), curated_keys(stack, "landing/")) == before


def test_same_event_twice_in_one_batch(stack):
    record = archive(stack, payment())
    assert process(stack, record, {**record, "messageId": "m-second"}) == []
    assert len(landed(stack)) == 1 and ledger(stack) == {"meridianpay#evt_1": "applied"}


def test_forced_replay_reprocesses_and_changes_nothing(stack):
    record = archive(stack, payment())
    process(stack, record)
    before = state(stack)
    forced = {**record, "body": json.dumps({**json.loads(record["body"]), "replay": True, "ignore_idempotency": True})}
    assert process(stack, forced) == []
    assert state(stack) == before and ledger(stack) == {"meridianpay#evt_1": "applied"}


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (b'{"id": "evt_1"', "malformed_json"),
        (payment(kind="disputed"), "unknown_event_type"),
        (b"[1, 2]", "schema_invalid"),
    ],
)
def test_unprocessable_payload_is_quarantined_not_retried(stack, body, reason):
    assert process(stack, archive(stack, body)) == []
    [key] = curated_keys(stack, "quarantine/")
    assert key == f"quarantine/vendor=meridianpay/reason={reason}/{hashlib.sha256(body).hexdigest()}.json"
    pointer = json.loads(stack.s3.get_object(Bucket=CURATED, Key=key)["Body"].read())
    assert pointer["archive_key"].startswith("raw/") and "payload" not in pointer
    assert ledger(stack) == {} and landed(stack) == []


def test_quarantine_write_is_create_only(stack):
    record = archive(stack, b"not json")
    process(stack, record)
    process(stack, record)
    assert len(stack.s3.list_object_versions(Bucket=CURATED, Prefix="quarantine/")["Versions"]) == 1


def test_poison_message_fails_alone(stack):
    """Partial batch response: the good messages in the batch are not retried, and the
    poison leaves nothing behind."""
    good, poison = (
        archive(stack, payment("evt_ok")),
        archive(stack, payment("evt_poison", payment_id="pay_x", amount=POISON_AMOUNT)),
    )
    assert process(stack, good, poison) == [poison["messageId"]]
    assert ledger(stack) == {"meridianpay#evt_ok": "applied"}
    assert [r["event_id"] for r in landed(stack)] == ["evt_ok"]
    assert state(stack, "payment#pay_x") is None


def test_pointer_that_does_not_match_the_archive_is_a_failure(stack):
    record = archive(stack, payment())
    pointer = json.loads(record["body"])
    wrong_digest = {**record, "body": json.dumps({**pointer, "sha256": "0" * 64})}
    wrong_bucket = {**record, "messageId": "m-2", "body": json.dumps({**pointer, "bucket": "someone-elses-bucket"})}
    assert process(stack, wrong_digest, wrong_bucket) == [wrong_digest["messageId"], "m-2"]
    assert ledger(stack) == {}


def test_event_is_recorded_only_after_it_has_landed(stack, monkeypatch):
    """If the landing write fails, the message is retried and the ledger has no record
    of it, so the retry does the work instead of skipping it as a duplicate."""
    record = archive(stack, payment())
    with monkeypatch.context() as patch:
        patch.setattr(stack.processor.landing, "write", lambda *_: (_ for _ in ()).throw(RuntimeError("S3 is down")))
        assert process(stack, record) == [record["messageId"]]
    assert ledger(stack) == {} and landed(stack) == []
    assert state(stack)["status"]["S"] == "settled"  # applied already; applying again is a no-op

    assert process(stack, record) == []
    assert ledger(stack) == {"meridianpay#evt_1": "already_applied"} and len(landed(stack)) == 1


def test_no_raw_pii_reaches_state_or_landing(stack):
    doc = {
        "notification_id": "ntf_1",
        "topic": "verification.report.completed",
        "sent_at": "2026-11-17T01:02:03.456Z",
        "report": {
            "report_id": "rpt_1",
            "revision": 3,
            "applicant_ref": "LA-000001",
            "state": "complete",
            "decision": "clear",
            "score_band": "B",
        },
        "subject": {
            "full_name": "José Núñez",
            "ssn_last4": "0042",
            "phone": "+1 (415) 555-0123",
            "date_of_birth": "1988-03-02",
        },
    }
    assert process(stack, archive(stack, json.dumps(doc).encode(), "veritybureau")) == []
    everything = json.dumps(
        [stack.ddb.scan(TableName="state")["Items"], stack.ddb.scan(TableName="ledger")["Items"], landed(stack)],
        ensure_ascii=False,
    )
    for raw in ("0042", "555-0123", "4155550123", "José", "1988-03-02"):
        assert raw not in everything
    assert "tok_" in everything
