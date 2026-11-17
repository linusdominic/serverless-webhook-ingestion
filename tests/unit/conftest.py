from __future__ import annotations

import pytest

from webhook_platform.pii import Tokenizer
from webhook_platform.signatures import SigningKey

PEPPER = b"unit-test-pepper-0123456789abcdef0123456789abcdef"


@pytest.fixture
def tokens() -> Tokenizer:
    return Tokenizer(PEPPER)


@pytest.fixture
def keys() -> list[SigningKey]:
    return [SigningKey("current", b"current-secret"), SigningKey("previous", b"previous-secret")]
