"""Raw archive layout. Pure.

Key: raw/vendor=<v>/dt=<YYYY-MM-DD>/hour=<HH>/<sha256 of body>.body

The partition comes from the vendor's signed timestamp rather than from the receive
time. It is authenticated, it is within the tolerance window of the receive time, and
it is identical for a retransmission of the same signed request, so a retry maps to
the same key and the conditional put turns it into a no-op.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timezone

RAW_PREFIX = "raw"
# S3 limits user metadata to 2 KB in total. Signature headers are small; anything
# pathological is truncated rather than allowed to fail the put.
_MAX_HEADER_VALUE = 512


def body_sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def archive_key(vendor: str, signed_at: float, sha256_hex: str) -> str:
    ts = datetime.fromtimestamp(signed_at, tz=timezone.utc)
    return f"{RAW_PREFIX}/vendor={vendor}/dt={ts:%Y-%m-%d}/hour={ts:%H}/{sha256_hex}.body"


def parse_archive_key(key: str) -> dict[str, str]:
    parts = key.split("/")
    if len(parts) != 5 or parts[0] != RAW_PREFIX or not parts[4].endswith(".body"):
        raise ValueError(f"not an archive key: {key}")
    out = {"sha256": parts[4][: -len(".body")]}
    for name, part in zip(("vendor", "dt", "hour"), parts[1:4], strict=True):
        prefix = f"{name}="
        if not part.startswith(prefix):
            raise ValueError(f"not an archive key: {key}")
        out[name] = part[len(prefix) :]
    return out


def pack_headers(headers: Mapping[str, str], keep: tuple[str, ...]) -> str:
    """Selected headers as base64(JSON), safe to store as S3 object metadata.

    Only the headers needed to re-verify the signature are kept. Archiving every
    header would capture whatever a proxy or the vendor decides to send, including
    credentials, into a bucket with a multi-year retention.
    """
    kept = {name: headers[name][:_MAX_HEADER_VALUE] for name in keep if name in headers}
    encoded = json.dumps(kept, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(encoded).decode("ascii")


def unpack_headers(packed: str) -> dict[str, str]:
    decoded = json.loads(base64.b64decode(packed))
    if not isinstance(decoded, dict):
        raise ValueError("packed headers are not an object")
    return {str(k): str(v) for k, v in decoded.items()}
