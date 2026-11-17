"""Vendor registry.

Each vendor signs differently. The differences that matter to the receiver are
captured here as data; the verification code lives in ``signatures``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Scheme(str, Enum):
    # HMAC-SHA256 over "<unix seconds>.<body>", hex, timestamp in its own header.
    TIMESTAMP_DOT_BODY_SHA256_HEX = "timestamp_dot_body_sha256_hex"
    # HMAC-SHA256 over "<path>\n<RFC 3339 time>\n<body>", base64.
    PATH_TIME_BODY_SHA256_B64 = "path_time_body_sha256_b64"
    # HMAC-SHA512 over "<unix millis>.<sha256 hex of body>", hex, with an explicit key id.
    KEYED_DIGEST_SHA512_HEX = "keyed_digest_sha512_hex"


@dataclass(frozen=True)
class VendorSpec:
    name: str
    scheme: Scheme
    signature_header: str
    timestamp_header: str | None
    # The path the vendor was given and therefore signs. It is configuration, not
    # event["rawPath"], because a custom domain or base path mapping changes what the
    # Lambda sees without changing what the vendor signed.
    signed_path: str

    @property
    def archived_headers(self) -> tuple[str, ...]:
        """Headers kept with the archived body: enough to re-verify the signature later."""
        names = [self.signature_header, "content-type"]
        if self.timestamp_header:
            names.append(self.timestamp_header)
        return tuple(names)


VENDORS: dict[str, VendorSpec] = {
    "meridianpay": VendorSpec(
        name="meridianpay",
        scheme=Scheme.TIMESTAMP_DOT_BODY_SHA256_HEX,
        signature_header="x-meridian-signature",
        timestamp_header="x-meridian-timestamp",
        signed_path="/webhooks/meridianpay",
    ),
    "callwright": VendorSpec(
        name="callwright",
        scheme=Scheme.PATH_TIME_BODY_SHA256_B64,
        signature_header="x-callwright-signature",
        timestamp_header="x-callwright-request-time",
        signed_path="/webhooks/callwright",
    ),
    "veritybureau": VendorSpec(
        name="veritybureau",
        scheme=Scheme.KEYED_DIGEST_SHA512_HEX,
        signature_header="verity-signature",
        timestamp_header=None,
        signed_path="/webhooks/veritybureau",
    ),
}


def get_vendor(name: str) -> VendorSpec | None:
    return VENDORS.get(name)
