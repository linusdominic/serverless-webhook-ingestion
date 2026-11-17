"""Receiver: API Gateway HTTP API -> verify -> archive -> enqueue -> 202.

The rule this function exists to enforce: a 2xx means the bytes are in S3. Nothing
is parsed here, because a vendor will not resend a payload we acknowledged, and a
parser bug must not be able to lose one. If either the archive write or the enqueue
fails, the vendor gets a 5xx and retries; both steps are safe to repeat.
"""

from __future__ import annotations

import base64
import json
import os
import time
from typing import Any

import boto3
from botocore.config import Config

from .. import log
from ..archive import archive_key, body_sha256, pack_headers
from ..config import env, env_int
from ..metrics import Metrics
from ..s3util import put_if_absent
from ..secrets import SecretCache, parse_signing_keys
from ..signatures import Reason, Verification, verify
from ..vendors import VendorSpec, get_vendor

# Short timeouts and a couple of retries: the vendor's own retry is the backstop, and
# a receiver that hangs for 30 seconds is worse for them than one that says 500.
_BOTO = Config(connect_timeout=2, read_timeout=5, retries={"max_attempts": 3, "mode": "standard"})
_s3 = boto3.client("s3", config=_BOTO)
_sqs = boto3.client("sqs", config=_BOTO)
_secrets = SecretCache(
    boto3.client("secretsmanager", config=_BOTO),
    ttl_seconds=env_int("SECRET_CACHE_TTL_SECONDS", 300),
)

_MAX_BODY_BYTES = env_int("MAX_BODY_BYTES", 1_048_576)
_TOLERANCE_SECONDS = env_int("SIGNATURE_TOLERANCE_SECONDS", 300)


def _response(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json", "cache-control": "no-store"},
        "body": json.dumps(body, separators=(",", ":")),
    }


def _raw_body(event: dict[str, Any]) -> bytes:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        return base64.b64decode(body)
    # API Gateway hands text bodies over as a str. Encoding it back to UTF-8 is exact
    # for any body that was valid UTF-8; one that was not has already been altered
    # upstream and will fail verification, which is the correct outcome.
    return str(body).encode("utf-8")


def _verify(spec: VendorSpec, headers: dict[str, str], body: bytes) -> Verification:
    secret_id = env("SIGNING_SECRET_PREFIX") + spec.name
    now = time.time()
    result = verify(
        spec,
        headers,
        body,
        parse_signing_keys(_secrets.get(secret_id)),
        now=now,
        tolerance_seconds=_TOLERANCE_SECONDS,
    )
    if result.reason in (Reason.BAD_SIGNATURE, Reason.UNKNOWN_KEY) and _secrets.refresh_if_allowed(secret_id):
        # The vendor may have rotated since this environment cached the secret.
        result = verify(
            spec,
            headers,
            body,
            parse_signing_keys(_secrets.get(secret_id)),
            now=now,
            tolerance_seconds=_TOLERANCE_SECONDS,
        )
    return result


def _archive(key: str, body: bytes, metadata: dict[str, str]) -> bool:
    """Write the body once. Returns True if it was already there."""
    created = put_if_absent(
        _s3,
        Bucket=env("RAW_BUCKET"),
        Key=key,
        Body=body,
        ContentType="application/octet-stream",
        Metadata=metadata,
        ServerSideEncryption="aws:kms",
        SSEKMSKeyId=env("RAW_KMS_KEY_ARN"),
        BucketKeyEnabled=True,
    )
    return not created


def _handle(event: dict[str, Any], context: Any, metrics: Metrics) -> dict[str, Any]:
    request_context = event.get("requestContext") or {}
    correlation_id = str(request_context.get("requestId") or getattr(context, "aws_request_id", "unknown"))
    vendor_name = str((event.get("pathParameters") or {}).get("vendor") or "")

    spec = get_vendor(vendor_name)
    if spec is None:
        # The vendor name is attacker-controlled, so it is neither logged verbatim
        # nor used as a metric dimension.
        metrics.add("RequestsRejected", Vendor="unknown", Reason="unknown_vendor")
        log.warning("unknown vendor path", correlation_id=correlation_id)
        return _response(404, {"error": "unknown_vendor"})

    body = _raw_body(event)
    if len(body) > _MAX_BODY_BYTES:
        metrics.add("RequestsRejected", Vendor=spec.name, Reason="body_too_large")
        log.warning("body too large", correlation_id=correlation_id, vendor=spec.name, bytes=len(body))
        return _response(413, {"error": "body_too_large"})

    headers = {str(k).lower(): str(v) for k, v in (event.get("headers") or {}).items()}
    result = _verify(spec, headers, body)
    if not result.ok or result.signed_at is None:
        metrics.add("RequestsRejected", Vendor=spec.name, Reason=result.reason.value)
        log.warning(
            "signature rejected",
            correlation_id=correlation_id,
            vendor=spec.name,
            reason=result.reason.value,
            key_id=result.key_id,
        )
        # One opaque answer for every verification failure.
        return _response(401, {"error": "signature_verification_failed"})

    sha256_hex = body_sha256(body)
    key = archive_key(spec.name, result.signed_at, sha256_hex)
    received_at = time.time()
    duplicate = _archive(
        key,
        body,
        {
            "vendor": spec.name,
            "sha256": sha256_hex,
            "signed-at": f"{result.signed_at:.3f}",
            "received-at": f"{received_at:.3f}",
            "correlation-id": correlation_id,
            "key-id": result.key_id or "",
            "signature-headers": pack_headers(headers, spec.archived_headers),
        },
    )

    # Enqueued even when the object already existed: the earlier attempt may have
    # archived and then failed before this line, which is why the vendor is retrying.
    _sqs.send_message(
        QueueUrl=env("QUEUE_URL"),
        MessageBody=json.dumps(
            {
                "schema": 1,
                "vendor": spec.name,
                "bucket": env("RAW_BUCKET"),
                "key": key,
                "sha256": sha256_hex,
                "correlation_id": correlation_id,
                "received_at": round(received_at, 3),
                "replay": False,
                "ignore_idempotency": False,
            },
            separators=(",", ":"),
        ),
        MessageAttributes={
            "vendor": {"DataType": "String", "StringValue": spec.name},
            "correlation_id": {"DataType": "String", "StringValue": correlation_id},
        },
    )

    metrics.add("RequestsAccepted", Vendor=spec.name)
    if duplicate:
        metrics.add("ArchiveDuplicates", Vendor=spec.name)
    metrics.add("BodyBytes", len(body), "Bytes", Vendor=spec.name)
    log.info(
        "accepted",
        correlation_id=correlation_id,
        vendor=spec.name,
        archive_key=key,
        duplicate=duplicate,
        bytes=len(body),
        key_id=result.key_id,
    )
    return _response(202, {"status": "accepted", "duplicate": duplicate, "correlation_id": correlation_id})


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    metrics = Metrics()
    try:
        return _handle(event, context, metrics)
    except Exception as exc:
        # Nothing was acknowledged. The vendor retries; both steps are idempotent.
        metrics.add("ReceiverErrors")
        log.exception("receiver failed", exc, function_version=os.environ.get("AWS_LAMBDA_FUNCTION_VERSION"))
        return _response(500, {"error": "internal_error"})
    finally:
        metrics.flush()
