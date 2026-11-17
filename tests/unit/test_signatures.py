"""Signature verification. Requests are signed by the vendor simulator, which is a
separate implementation of each scheme, so these tests also pin the two to each other."""

from __future__ import annotations

import pytest

from webhook_platform.signatures import Reason, SigningKey, verify
from webhook_platform.vendors import VENDORS
from webhook_tools.vendorsim import Key, sign

NOW = 1_794_870_000.0
BODY = b'{"id":"evt_1","amount":125000}'
ALL = sorted(VENDORS)


def signed(
    vendor: str, body: bytes = BODY, *, secret: bytes = b"current-secret", key_id: str = "current", at: float = NOW
) -> dict[str, str]:
    return {name.lower(): value for name, value in sign(vendor, body, Key(key_id, secret), at).items()}


def check(vendor: str, headers: dict[str, str], keys: list[SigningKey], body: bytes = BODY, now: float = NOW):
    return verify(VENDORS[vendor], headers, body, keys, now=now, tolerance_seconds=300)


@pytest.mark.parametrize("vendor", ALL)
def test_valid_signature_is_accepted(vendor, keys):
    result = check(vendor, signed(vendor), keys)
    assert result.ok and result.reason is Reason.OK
    assert result.key_id == "current"
    assert result.signed_at == pytest.approx(NOW, abs=1)


@pytest.mark.parametrize("vendor", ALL)
def test_previous_key_is_accepted_during_rotation(vendor, keys):
    result = check(vendor, signed(vendor, secret=b"previous-secret", key_id="previous"), keys)
    assert result.ok and result.key_id == "previous"


@pytest.mark.parametrize("vendor", ALL)
def test_altered_body_is_rejected(vendor, keys):
    result = check(vendor, signed(vendor), keys, body=BODY.replace(b"125000", b"925000"))
    assert not result.ok and result.reason is Reason.BAD_SIGNATURE


@pytest.mark.parametrize("vendor", ALL)
def test_single_trailing_byte_is_rejected(vendor, keys):
    """The archive is byte-exact because the signature is: whitespace counts."""
    assert check(vendor, signed(vendor), keys, body=BODY + b"\n").reason is Reason.BAD_SIGNATURE


@pytest.mark.parametrize("vendor", ALL)
def test_wrong_secret_is_rejected(vendor, keys):
    assert check(vendor, signed(vendor, secret=b"someone-else"), keys).reason is Reason.BAD_SIGNATURE


@pytest.mark.parametrize("vendor", ALL)
@pytest.mark.parametrize("offset", [-301, 301, -3600, 86400])
def test_authentic_request_outside_tolerance_is_rejected(vendor, keys, offset):
    result = check(vendor, signed(vendor, at=NOW + offset), keys)
    assert not result.ok and result.reason is Reason.TIMESTAMP_OUT_OF_TOLERANCE
    assert result.key_id == "current"  # the MAC was checked first and was good


@pytest.mark.parametrize("vendor", ALL)
@pytest.mark.parametrize("offset", [-299, 0, 299])
def test_within_tolerance_is_accepted(vendor, keys, offset):
    assert check(vendor, signed(vendor, at=NOW + offset), keys).ok


@pytest.mark.parametrize("vendor", ALL)
def test_forged_request_with_stale_timestamp_reports_bad_signature(vendor, keys):
    """Timestamp is only trusted after the MAC verifies."""
    result = check(vendor, signed(vendor, secret=b"someone-else", at=NOW - 9999), keys)
    assert result.reason is Reason.BAD_SIGNATURE


@pytest.mark.parametrize("vendor", ALL)
def test_missing_headers_are_rejected(vendor, keys):
    assert check(vendor, {}, keys).reason is Reason.MISSING_HEADER
    assert check(vendor, {"content-type": "application/json"}, keys).reason is Reason.MISSING_HEADER


def test_missing_timestamp_header_is_rejected(keys):
    headers = signed("meridianpay")
    del headers["x-meridian-timestamp"]
    assert check("meridianpay", headers, keys).reason is Reason.MISSING_HEADER


@pytest.mark.parametrize(
    ("vendor", "headers"),
    [
        ("meridianpay", {"x-meridian-timestamp": "1794870000", "x-meridian-signature": "sha256=zz"}),
        ("meridianpay", {"x-meridian-timestamp": "1794870000", "x-meridian-signature": "md5=abcd"}),
        ("meridianpay", {"x-meridian-timestamp": "soon", "x-meridian-signature": "sha256=abcd"}),
        ("meridianpay", {"x-meridian-timestamp": "-5", "x-meridian-signature": "sha256=abcd"}),
        ("meridianpay", {"x-meridian-timestamp": "１２３", "x-meridian-signature": "sha256=abcd"}),
        ("callwright", {"x-callwright-request-time": "yesterday", "x-callwright-signature": "AAAA"}),
        ("callwright", {"x-callwright-request-time": "2026-11-17T01:00:00", "x-callwright-signature": "AAAA"}),
        ("callwright", {"x-callwright-request-time": "2026-11-17T01:00:00Z", "x-callwright-signature": "not base64!"}),
        ("veritybureau", {"verity-signature": "sig=abcd"}),
        ("veritybureau", {"verity-signature": "keyid=k;ts=12;sig=abcd;sig=ef"}),
        ("veritybureau", {"verity-signature": "keyid=k;ts=noon;sig=abcd"}),
        ("veritybureau", {"verity-signature": "keyid=;ts=12;sig=abcd"}),
        ("veritybureau", {"verity-signature": "keyid=k;ts=12;sig=xyz"}),
    ],
)
def test_malformed_headers_are_rejected_not_raised(vendor, headers, keys):
    assert check(vendor, headers, keys).reason is Reason.MALFORMED_HEADER


def test_unknown_key_id_is_rejected(keys):
    result = check("veritybureau", signed("veritybureau", key_id="retired-2019"), keys)
    assert result.reason is Reason.UNKNOWN_KEY


def test_key_id_selects_the_key(keys):
    """A valid MAC under the previous secret, presented under the current key's id, fails."""
    result = check("veritybureau", signed("veritybureau", secret=b"previous-secret", key_id="current"), keys)
    assert result.reason is Reason.BAD_SIGNATURE


def test_signature_does_not_transfer_between_vendors(keys):
    """Same secret, same body: a Meridian signature must not verify as Callwright's."""
    headers = signed("meridianpay")
    forged = {
        "x-callwright-request-time": "2026-11-17T01:00:00Z",
        "x-callwright-signature": headers["x-meridian-signature"],
    }
    assert not check("callwright", forged, keys).ok


def test_callwright_signature_covers_the_path(keys):
    from dataclasses import replace

    other_path = replace(VENDORS["callwright"], signed_path="/webhooks/other")
    result = verify(other_path, signed("callwright"), BODY, keys, now=NOW, tolerance_seconds=300)
    assert result.reason is Reason.BAD_SIGNATURE


def test_no_keys_means_nothing_verifies():
    assert check("meridianpay", signed("meridianpay"), []).reason is Reason.BAD_SIGNATURE


def test_empty_body_can_be_signed_and_verified(keys):
    assert check("meridianpay", signed("meridianpay", body=b""), keys, body=b"").ok
