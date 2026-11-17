"""Webhook signature verification. Pure: no clock, no network, no AWS.

The order of checks is deliberate. The MAC is checked before the timestamp so that
"authentic but stale" (vendor retry lag, clock skew, or a captured request being
replayed) can be told apart from "not authentic" in metrics. Both are rejected.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from .vendors import Scheme, VendorSpec


class Reason(str, Enum):
    OK = "ok"
    MISSING_HEADER = "missing_header"
    MALFORMED_HEADER = "malformed_header"
    UNKNOWN_KEY = "unknown_key"
    BAD_SIGNATURE = "bad_signature"
    TIMESTAMP_OUT_OF_TOLERANCE = "timestamp_out_of_tolerance"


@dataclass(frozen=True)
class SigningKey:
    key_id: str
    secret: bytes


@dataclass(frozen=True)
class Verification:
    ok: bool
    reason: Reason
    # Seconds since the epoch, taken from the signed timestamp. Authenticated once ok
    # is True, which is why the archive partition is derived from it.
    signed_at: float | None = None
    key_id: str | None = None


def _fail(reason: Reason) -> Verification:
    return Verification(ok=False, reason=reason)


@dataclass(frozen=True)
class _Parsed:
    signed_at: float
    provided_mac: bytes
    key_id: str | None
    message: bytes
    digestmod: str


def _parse_rfc3339(value: str) -> float:
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no offset")
    return parsed.astimezone(timezone.utc).timestamp()


def _parse(spec: VendorSpec, headers: Mapping[str, str], body: bytes) -> _Parsed | Reason:
    raw_sig = headers.get(spec.signature_header)
    if not raw_sig:
        return Reason.MISSING_HEADER

    try:
        if spec.scheme is Scheme.TIMESTAMP_DOT_BODY_SHA256_HEX:
            assert spec.timestamp_header is not None
            raw_ts = headers.get(spec.timestamp_header)
            if not raw_ts:
                return Reason.MISSING_HEADER
            if not raw_ts.isascii() or not raw_ts.isdigit():
                return Reason.MALFORMED_HEADER
            prefix, _, hexmac = raw_sig.partition("=")
            if prefix != "sha256" or not hexmac:
                return Reason.MALFORMED_HEADER
            return _Parsed(
                signed_at=float(int(raw_ts)),
                provided_mac=bytes.fromhex(hexmac),
                key_id=None,
                message=raw_ts.encode("ascii") + b"." + body,
                digestmod="sha256",
            )

        if spec.scheme is Scheme.PATH_TIME_BODY_SHA256_B64:
            assert spec.timestamp_header is not None
            raw_ts = headers.get(spec.timestamp_header)
            if not raw_ts:
                return Reason.MISSING_HEADER
            return _Parsed(
                signed_at=_parse_rfc3339(raw_ts),
                provided_mac=base64.b64decode(raw_sig, validate=True),
                key_id=None,
                message=spec.signed_path.encode() + b"\n" + raw_ts.encode() + b"\n" + body,
                digestmod="sha256",
            )

        if spec.scheme is Scheme.KEYED_DIGEST_SHA512_HEX:
            fields: dict[str, str] = {}
            for part in raw_sig.split(";"):
                name, sep, value = part.strip().partition("=")
                if not sep or name in fields:
                    return Reason.MALFORMED_HEADER
                fields[name] = value
            if set(fields) != {"keyid", "ts", "sig"}:
                return Reason.MALFORMED_HEADER
            if not fields["ts"].isascii() or not fields["ts"].isdigit() or not fields["keyid"]:
                return Reason.MALFORMED_HEADER
            body_digest = hashlib.sha256(body).hexdigest()
            return _Parsed(
                signed_at=int(fields["ts"]) / 1000.0,
                provided_mac=bytes.fromhex(fields["sig"]),
                key_id=fields["keyid"],
                message=f"{fields['ts']}.{body_digest}".encode("ascii"),
                digestmod="sha512",
            )
    except (ValueError, binascii.Error, UnicodeError, OverflowError):
        return Reason.MALFORMED_HEADER

    raise AssertionError(f"unhandled scheme {spec.scheme}")  # pragma: no cover


def verify(
    spec: VendorSpec,
    headers: Mapping[str, str],
    body: bytes,
    keys: Sequence[SigningKey],
    *,
    now: float,
    tolerance_seconds: int,
) -> Verification:
    """Verify one request.

    ``headers`` must have lower-case names. ``keys`` is every key currently valid for
    the vendor (current first, then previous), which is what makes rotation a
    non-event: both verify until the old key is removed from the secret.
    """
    parsed = _parse(spec, headers, body)
    if isinstance(parsed, Reason):
        return _fail(parsed)

    candidates = list(keys)
    if parsed.key_id is not None:
        candidates = [k for k in keys if k.key_id == parsed.key_id]
        if not candidates:
            return _fail(Reason.UNKNOWN_KEY)

    matched: SigningKey | None = None
    for key in candidates:
        expected = hmac.new(key.secret, parsed.message, parsed.digestmod).digest()
        # compare_digest on every candidate; no early exit on length or content.
        if hmac.compare_digest(expected, parsed.provided_mac) and matched is None:
            matched = key
    if matched is None:
        return _fail(Reason.BAD_SIGNATURE)

    if abs(now - parsed.signed_at) > tolerance_seconds:
        return Verification(
            ok=False,
            reason=Reason.TIMESTAMP_OUT_OF_TOLERANCE,
            signed_at=parsed.signed_at,
            key_id=matched.key_id,
        )
    return Verification(ok=True, reason=Reason.OK, signed_at=parsed.signed_at, key_id=matched.key_id)
