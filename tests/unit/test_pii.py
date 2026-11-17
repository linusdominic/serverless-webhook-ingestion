from __future__ import annotations

import pytest

from webhook_platform.pii import TOKEN_PREFIX, Tokenizer, normalize_phone, tokenize
from webhook_tools.vendorsim import phone_token, pii_token

PEPPER = b"p" * 32


def test_token_is_stable_and_opaque():
    token = tokenize(PEPPER, "phone", "+14155550123")
    assert token == tokenize(PEPPER, "phone", "+14155550123")
    assert token.startswith(TOKEN_PREFIX) and len(token) == len(TOKEN_PREFIX) + 32
    assert "4155550123" not in token


def test_different_pepper_different_token():
    assert tokenize(PEPPER, "phone", "+14155550123") != tokenize(b"q" * 32, "phone", "+14155550123")


def test_kinds_are_domain_separated():
    assert tokenize(PEPPER, "phone", "1234") != tokenize(PEPPER, "ssn_last4", "1234")


@pytest.mark.parametrize("raw", ["+1 (415) 555-0123", "+1-415-555-0123", "1 415 555 0123", "+14155550123"])
def test_phone_formats_normalise_to_one_token(raw):
    tokens = Tokenizer(PEPPER)
    assert normalize_phone(raw) == "+14155550123"
    assert tokens.phone(raw) == tokens.phone("+14155550123")


def test_ssn_fragment_is_tokenised_not_hashed():
    """An unkeyed digest of four digits is a lookup table. The token must depend on the key."""
    import hashlib

    token = Tokenizer(PEPPER).ssn_last4("1234")
    assert hashlib.sha256(b"1234").hexdigest()[:32] not in token
    assert token != Tokenizer(b"x" * 32).ssn_last4("1234")


def test_empty_pepper_is_refused():
    with pytest.raises(ValueError, match="empty pepper"):
        Tokenizer(b"")
    with pytest.raises(ValueError, match="empty pepper"):
        tokenize(b"", "phone", "1")


def test_verifier_computes_the_same_tokens_independently():
    tokens = Tokenizer(PEPPER)
    assert tokens.phone("+1 (415) 555-0123") == phone_token(PEPPER, "+1 (415) 555-0123")
    assert tokens.ssn_last4("0042") == pii_token(PEPPER, "ssn_last4", "0042")
