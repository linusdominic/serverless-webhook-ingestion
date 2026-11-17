from __future__ import annotations

import json

import pytest

from webhook_platform import log
from webhook_platform.metrics import Metrics
from webhook_platform.secrets import SecretCache, parse_pepper, parse_signing_keys


class FakeSecretsManager:
    def __init__(self, value: str) -> None:
        self.value, self.calls = value, 0

    def get_secret_value(self, SecretId: str) -> dict:
        self.calls += 1
        return {"SecretString": self.value}


def test_secret_is_fetched_once_within_the_ttl():
    client = FakeSecretsManager("v1")
    cache = SecretCache(client, ttl_seconds=300)
    assert [cache.get("s", now=t) for t in (0, 1, 299)] == ["v1"] * 3
    assert client.calls == 1


def test_secret_is_refetched_after_the_ttl():
    client = FakeSecretsManager("v1")
    cache = SecretCache(client, ttl_seconds=300)
    cache.get("s", now=0)
    client.value = "v2"
    assert cache.get("s", now=301) == "v2" and client.calls == 2


def test_forced_refresh_picks_up_a_rotation_and_is_rate_limited():
    client = FakeSecretsManager("v1")
    cache = SecretCache(client, ttl_seconds=300, min_refresh_interval_seconds=30)
    cache.get("s", now=0)
    client.value = "v2"
    assert cache.refresh_if_allowed("s", now=10) is False  # too soon: a flood of forgeries costs nothing
    assert client.calls == 1
    assert cache.refresh_if_allowed("s", now=31) is True
    assert cache.get("s", now=32) == "v2" and client.calls == 2
    assert cache.refresh_if_allowed("s", now=100) is False  # refetched, unchanged
    assert client.calls == 3


def test_parse_signing_keys_keeps_order():
    keys = parse_signing_keys(json.dumps({"keys": [{"id": "new", "secret": "a"}, {"id": "old", "secret": "b"}]}))
    assert [(k.key_id, k.secret) for k in keys] == [("new", b"a"), ("old", b"b")]


@pytest.mark.parametrize("value", ['{"keys": []}', '{"keys": [{"id": "k", "secret": ""}]}'])
def test_unusable_signing_secret_is_an_error(value):
    with pytest.raises(ValueError, match="no usable keys"):
        parse_signing_keys(value)


def test_short_pepper_is_refused():
    with pytest.raises(ValueError, match="shorter than 32"):
        parse_pepper('{"pepper": "short"}')
    assert parse_pepper(json.dumps({"pepper": "x" * 32})) == b"x" * 32


def test_metrics_are_emitted_in_embedded_metric_format(capsys):
    metrics = Metrics()
    metrics.add("RequestsRejected", Vendor="meridianpay", Reason="bad_signature")
    metrics.add("RequestsRejected", Vendor="meridianpay", Reason="bad_signature")
    metrics.add("BodyBytes", 512, "Bytes", Vendor="meridianpay")
    lines = [json.loads(line) for line in metrics.flush()]
    assert [json.loads(line) for line in capsys.readouterr().out.splitlines()] == lines
    by_metric = {tuple(m["Name"] for m in doc["_aws"]["CloudWatchMetrics"][0]["Metrics"]): doc for doc in lines}
    rejected = by_metric[("RequestsRejected",)]
    assert rejected["RequestsRejected"] == 2
    directive = rejected["_aws"]["CloudWatchMetrics"][0]
    assert directive["Namespace"] == "WebhookIngestion"
    assert directive["Dimensions"] == [["Function", "Reason", "Vendor"]]
    assert all(name in rejected for name in directive["Dimensions"][0])
    assert by_metric[("BodyBytes",)]["_aws"]["CloudWatchMetrics"][0]["Metrics"][0]["Unit"] == "Bytes"
    assert metrics.flush() == []  # flushed once


def test_log_lines_are_json_with_a_fixed_envelope(capsys):
    log.info("accepted", correlation_id="abc", vendor="meridianpay", key_id=None)
    line = json.loads(capsys.readouterr().out)
    assert line["level"] == "INFO" and line["message"] == "accepted" and line["correlation_id"] == "abc"
    assert "key_id" not in line and {"ts", "service", "function"} <= set(line)


def test_exception_logging_has_no_traceback_or_locals(capsys):
    payload = '{"ssn_last4": "0042"}'
    try:
        raise ValueError("bad thing")
    except ValueError as exc:
        log.exception("processing failed", exc, message_id="m1")
    out = capsys.readouterr().out
    line = json.loads(out)
    assert line["error_type"] == "ValueError" and line["error"] == "bad thing"
    assert "Traceback" not in out and payload not in out
