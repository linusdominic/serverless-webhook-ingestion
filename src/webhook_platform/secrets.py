"""Secrets Manager access with an in-memory cache.

Secrets are read at runtime by name. They are never placed in environment variables
(visible in the Lambda console and in Terraform state) and never logged.

The cache lives for the life of the execution environment. Two things bound its
staleness: a TTL, and a forced refresh the receiver triggers when a signature fails
to verify. The forced refresh is rate limited per secret so that a flood of forged
requests cannot be turned into a flood of GetSecretValue calls.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from .signatures import SigningKey


@dataclass
class _Entry:
    value: str
    fetched_at: float


class SecretCache:
    def __init__(self, client: Any, *, ttl_seconds: float = 300.0, min_refresh_interval_seconds: float = 30.0) -> None:
        self._client = client
        self._ttl = ttl_seconds
        self._min_refresh = min_refresh_interval_seconds
        self._entries: dict[str, _Entry] = {}

    def get(self, secret_id: str, *, now: float | None = None) -> str:
        current = time.monotonic() if now is None else now
        entry = self._entries.get(secret_id)
        if entry is None or current - entry.fetched_at > self._ttl:
            entry = self._fetch(secret_id, current)
        return entry.value

    def refresh_if_allowed(self, secret_id: str, *, now: float | None = None) -> bool:
        """Re-read the secret unless it was read very recently. Returns True if it changed."""
        current = time.monotonic() if now is None else now
        entry = self._entries.get(secret_id)
        if entry is not None and current - entry.fetched_at < self._min_refresh:
            return False
        previous = entry.value if entry else None
        return self._fetch(secret_id, current).value != previous

    def _fetch(self, secret_id: str, now: float) -> _Entry:
        response = self._client.get_secret_value(SecretId=secret_id)
        entry = _Entry(value=response["SecretString"], fetched_at=now)
        self._entries[secret_id] = entry
        return entry


def parse_signing_keys(secret_string: str) -> list[SigningKey]:
    """Secret format: {"keys": [{"id": "...", "secret": "..."}, ...]}, current key first.

    A list rather than a single value so that a rotation is: add the new key, wait
    for the vendor to switch, remove the old key. No deploy, no dropped webhooks.
    """
    doc = json.loads(secret_string)
    keys = [SigningKey(key_id=str(k["id"]), secret=str(k["secret"]).encode("utf-8")) for k in doc["keys"]]
    if not keys or any(not k.secret for k in keys):
        raise ValueError("signing secret has no usable keys")
    return keys


def parse_pepper(secret_string: str) -> bytes:
    pepper = str(json.loads(secret_string)["pepper"]).encode("utf-8")
    if len(pepper) < 32:
        raise ValueError("PII pepper is shorter than 32 bytes")
    return pepper
