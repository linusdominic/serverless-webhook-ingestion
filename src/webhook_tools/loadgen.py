"""Send the seeded workload and record what was sent and what came back.

Writes .run/manifest.jsonl (one line per delivery: the exact bytes, the signing
timestamp, the response, and what should happen downstream) and
.run/loadgen-summary.json (counts, throughput, latency percentiles).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import random
import secrets as pysecrets
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import requests
from requests.adapters import HTTPAdapter

from . import plan as planner
from . import sandbox
from .vendorsim import Key, effective_signed_at, iso_ms, sign

_RETRYABLE = {500, 502, 503, 504}
_MAX_ATTEMPTS = 4


def load_keys() -> dict[str, list[Key]]:
    """Signing keys, read from the emulator's Secrets Manager. Never written to disk."""
    sm = sandbox.client("secretsmanager")
    keys = {}
    for vendor, name in sandbox.outputs()["signing_secret_names"].items():
        doc = json.loads(sm.get_secret_value(SecretId=name)["SecretString"])
        keys[vendor] = [Key(k["id"], k["secret"].encode()) for k in doc["keys"]]
    return keys


class Sender:
    def __init__(self, deliveries: list[planner.Delivery], keys: dict[str, list[Key]], seed: int) -> None:
        self._deliveries = deliveries
        self._keys = keys
        self._bogus = random.Random(seed ^ 0x5EED)
        self._lock = threading.Lock()
        self._signed: dict[int, tuple[dict[str, str], float, str]] = {}
        self._local = threading.local()

    def _session(self) -> requests.Session:
        if not hasattr(self._local, "session"):
            session = requests.Session()
            session.mount("http://", HTTPAdapter(pool_connections=4, pool_maxsize=4))
            self._local.session = session
        return self._local.session  # type: ignore[no-any-return]

    def _headers(self, delivery: planner.Delivery) -> tuple[dict[str, str], float | None, str | None]:
        """Signature headers, the timestamp they carry, and the key id used."""
        vendor = delivery.path_vendor if delivery.path_vendor in self._keys else "meridianpay"
        current, previous = self._keys[vendor][0], self._keys[vendor][1]
        now = time.time()
        kind = delivery.signing

        if kind == "reuse":
            assert delivery.reuse_of is not None
            return self._signed_once(self._deliveries[delivery.reuse_of])
        if kind == "missing_signature":
            return {}, None, None
        if kind == "malformed_signature":
            garbage = {
                "meridianpay": {"X-Meridian-Timestamp": str(int(now)), "X-Meridian-Signature": "sha256=not-hex"},
                "callwright": {"X-Callwright-Request-Time": "yesterday", "X-Callwright-Signature": "AAAA"},
                "veritybureau": {"Verity-Signature": "sig=abc"},
            }
            return garbage[vendor], None, None

        key, at, body = current, now, delivery.sign_body or delivery.body
        if kind == "previous_key":
            key = previous
        elif kind == "wrong_secret":
            key = Key(current.key_id, pysecrets.token_bytes(32))
        elif kind == "unknown_key_id":
            key = Key("retired-2019", pysecrets.token_bytes(32))
        elif kind == "stale":
            at = now - 3600
        elif kind == "future":
            at = now + 3600
        return sign(vendor, body, key, at), effective_signed_at(vendor, at), key.key_id

    def _signed_once(self, delivery: planner.Delivery) -> tuple[dict[str, str], float, str]:
        """Sign a delivery the first time it is needed and keep the result, so that an
        exact retransmission carries the same headers whichever copy is sent first."""
        with self._lock:
            if delivery.index not in self._signed:
                headers, signed_at, key_id = self._headers(delivery)
                assert signed_at is not None
                assert key_id is not None
                self._signed[delivery.index] = (headers, signed_at, key_id)
            return self._signed[delivery.index]

    def send(self, delivery: planner.Delivery) -> dict[str, Any]:
        if delivery.signing in ("valid", "previous_key", "reuse") and delivery.expected_status == 202:
            headers, signed_at, key_id = (
                self._signed_once(delivery) if delivery.signing != "reuse" else self._headers(delivery)
            )
        else:
            headers, signed_at, key_id = self._headers(delivery)

        url = sandbox.webhook_url(delivery.path_vendor)
        status, attempts, started = 0, 0, time.perf_counter()
        latency = 0.0
        while attempts < _MAX_ATTEMPTS:
            attempts += 1
            attempt_started = time.perf_counter()
            try:
                response = self._session().post(
                    url, data=delivery.body, headers={**headers, "Content-Type": "application/json"}, timeout=60
                )
                status = response.status_code
            except requests.RequestException:
                status = 0
            latency = time.perf_counter() - attempt_started
            if status not in _RETRYABLE and status != 0:
                break
            time.sleep(0.2 * attempts)  # a vendor retries a 5xx; so does this
        return {
            "status": status,
            "attempts": attempts,
            "latency_ms": round(latency * 1000, 2),
            "total_ms": round((time.perf_counter() - started) * 1000, 2),
            "signed_at": signed_at,
            "key_id": key_id,
        }


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    return sorted_values[min(len(sorted_values) - 1, int(q * len(sorted_values)))]


def _event_record(event: dict[str, Any] | None) -> dict[str, Any] | None:
    if event is None:
        return None
    return {**event, "occurred_at": iso_ms(event["occurred_at"])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=20261117)
    parser.add_argument(
        "--events", type=int, default=3000, help="number of genuine events; deliveries are about 17%% more"
    )
    parser.add_argument("--concurrency", type=int, default=16)
    args = parser.parse_args()

    base_time = datetime.now(tz=timezone.utc).replace(microsecond=0)
    deliveries = planner.build(args.seed, args.events, base_time)
    sender = Sender(deliveries, load_keys(), args.seed)
    categories = Counter(d.category for d in deliveries)

    print(
        f"loadgen: seed {args.seed}, {args.events} events requested, {len(deliveries)} deliveries, "
        f"concurrency {args.concurrency}"
    )
    print(
        f"loadgen: plan fingerprint {planner.fingerprint(deliveries)[:16]} "
        f"(seed + event count + base time {iso_ms(base_time)})"
    )

    started_at = datetime.now(tz=timezone.utc)
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(sender.send, deliveries))
    elapsed = time.perf_counter() - started

    sandbox.RUN_DIR.mkdir(parents=True, exist_ok=True)
    with (sandbox.RUN_DIR / "manifest.jsonl").open("w") as handle:
        for delivery, sent in zip(deliveries, results, strict=True):
            handle.write(
                json.dumps(
                    {
                        "index": delivery.index,
                        "path_vendor": delivery.path_vendor,
                        "category": delivery.category,
                        "signing": delivery.signing,
                        "reuse_of": delivery.reuse_of,
                        "expected_status": delivery.expected_status,
                        "expect": delivery.expect,
                        "body_sha256": hashlib.sha256(delivery.body).hexdigest(),
                        "body_b64": base64.b64encode(delivery.body).decode(),
                        "event": _event_record(delivery.event),
                        "sent": sent,
                    },
                    separators=(",", ":"),
                )
                + "\n"
            )

    statuses = Counter(r["status"] for r in results)
    unexpected = [d.index for d, r in zip(deliveries, results, strict=True) if r["status"] != d.expected_status]
    accepted = sorted(r["latency_ms"] for r in results if r["status"] == 202)
    everything = sorted(r["latency_ms"] for r in results)
    summary = {
        "seed": args.seed,
        "events_requested": args.events,
        "base_time": iso_ms(base_time),
        "plan_fingerprint": planner.fingerprint(deliveries),
        "started_at": iso_ms(started_at),
        "deliveries": len(deliveries),
        "concurrency": args.concurrency,
        "wall_seconds": round(elapsed, 2),
        "requests_per_second": round(len(deliveries) / elapsed, 1),
        "by_category": dict(sorted(categories.items())),
        "by_status": {str(k): v for k, v in sorted(statuses.items())},
        "retried_requests": sum(1 for r in results if r["attempts"] > 1),
        "responses_not_as_expected": len(unexpected),
        "entities_delivered_out_of_order": planner.reordered_entities(deliveries),
        "latency_ms_accepted": {
            "p50": _percentile(accepted, 0.50),
            "p95": _percentile(accepted, 0.95),
            "p99": _percentile(accepted, 0.99),
            "max": accepted[-1] if accepted else 0.0,
        },
        "latency_ms_all": {
            "p50": _percentile(everything, 0.50),
            "p95": _percentile(everything, 0.95),
            "p99": _percentile(everything, 0.99),
            "max": everything[-1] if everything else 0.0,
        },
    }
    sandbox.write_json("loadgen-summary.json", summary)
    sandbox.write_json("replays.json", {"count": 0})

    print(f"loadgen: sent {len(deliveries)} in {elapsed:.1f}s = {summary['requests_per_second']} req/s")
    print(f"loadgen: by category   {dict(sorted(categories.items()))}")
    print(f"loadgen: by status     {dict(sorted(statuses.items()))}")
    lat = summary["latency_ms_accepted"]
    print(
        f"loadgen: latency of accepted requests  p50 {lat['p50']} ms  p95 {lat['p95']} ms  "
        f"p99 {lat['p99']} ms  max {lat['max']} ms"
    )
    print(
        f"loadgen: {summary['retried_requests']} request(s) needed a retry; "
        f"{len(unexpected)} response(s) differed from the plan"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
