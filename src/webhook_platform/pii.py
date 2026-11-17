"""PII handling. Pure.

Phone numbers and SSN fragments never leave the raw bucket in the clear. They are
replaced with a keyed token: HMAC-SHA256 under a pepper held in Secrets Manager.

A plain hash would not do. The last four digits of an SSN have 10,000 possible
values and a phone number about 10^10, so an unkeyed digest is reversible by
enumeration in seconds. With the pepper, a token is stable (the same number always
maps to the same token, so joins and counts still work) and is only reversible by
someone who can read the secret.
"""

from __future__ import annotations

import hashlib
import hmac

TOKEN_PREFIX = "tok_"  # noqa: S105 - a label, not a credential
_TOKEN_HEX_CHARS = 32  # 128 bits


def normalize_phone(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    return "+" + digits


def tokenize(pepper: bytes, kind: str, value: str) -> str:
    """Token for ``value``. ``kind`` domain-separates fields so that an SSN fragment
    and a phone extension that happen to be equal do not produce equal tokens."""
    if not pepper:
        raise ValueError("empty pepper")
    mac = hmac.new(pepper, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()
    return TOKEN_PREFIX + mac[:_TOKEN_HEX_CHARS]


class Tokenizer:
    def __init__(self, pepper: bytes) -> None:
        if not pepper:
            raise ValueError("empty pepper")
        self._pepper = pepper

    def phone(self, value: str) -> str:
        return tokenize(self._pepper, "phone", normalize_phone(value))

    def ssn_last4(self, value: str) -> str:
        return tokenize(self._pepper, "ssn_last4", value.strip())
