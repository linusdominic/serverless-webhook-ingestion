"""Check the deployed system against the load generator's ground truth.

Each invariant is a function that compares ``Truth`` (what was sent, and what should
have become of it) with ``World`` (what is in S3, DynamoDB, SQS, Glue, Athena, Step
Functions and CloudWatch). One line per invariant; exit status 1 if any fails.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import time
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from typing import Any

from . import sandbox
from .world import Truth, World, sha256_hex

Result = tuple[bool, str]
Check = Callable[[Truth, World], Result]
CHECKS: list[tuple[str, Check]] = []

_PHONE = re.compile(r"\+1[ (]*\d{3}[) ]*\d{3}-?\d{4}")
_DATE = re.compile(r"\b(?:19|20)\d\d-\d\d-\d\d\b")
_RAW_FIELDS = ('"phone"', '"from"', '"to"', '"ssn_last4"', '"full_name"', '"date_of_birth"')


def invariant(name: str) -> Callable[[Check], Check]:
    def register(check: Check) -> Check:
        CHECKS.append((name, check))
        return check

    return register


def _examples(items: Any, limit: int = 3) -> str:
    listed = sorted(str(i) for i in items)
    more = f" (+{len(listed) - limit} more)" if len(listed) > limit else ""
    return ", ".join(listed[:limit]) + more


def _set_diff(label: str, expected: set[Any], actual: set[Any]) -> Result:
    missing, extra = expected - actual, actual - expected
    if not missing and not extra:
        return True, f"{len(actual)} {label}"
    parts = []
    if missing:
        parts.append(f"{len(missing)} missing: {_examples(missing)}")
    if extra:
        parts.append(f"{len(extra)} unexpected: {_examples(extra)}")
    return False, f"{label}: " + "; ".join(parts)


# --- receive ----------------------------------------------------------------------


@invariant("every response matches the plan")
def responses(truth: Truth, world: World) -> Result:
    wrong = [d["index"] for d in truth.deliveries if d["sent"]["status"] != d["expected_status"]]
    counts = Counter(d["sent"]["status"] for d in truth.deliveries)
    summary = ", ".join(f"{count} x {status}" for status, count in sorted(counts.items()))
    return not wrong, f"{len(truth.deliveries)} requests: {summary}" + (f"; wrong: {_examples(wrong)}" if wrong else "")


@invariant("every accepted request is archived, byte-identical")
def archive_complete(truth: Truth, world: World) -> Result:
    missing = [key for key in truth.archive if key not in world.raw_objects]
    altered = [
        key for key, body in truth.archive.items() if key in world.raw_objects and world.raw_objects[key] != body
    ]
    if missing or altered:
        return False, f"{len(missing)} missing: {_examples(missing)}; {len(altered)} altered: {_examples(altered)}"
    total = sum(len(body) for body in truth.archive.values())
    return (
        True,
        f"{len(truth.accepted)} accepted requests -> {len(truth.archive)} distinct objects, {total:,} bytes compared",
    )


@invariant("the archive holds each object exactly once and nothing else")
def archive_exact(truth: Truth, world: World) -> Result:
    ok, detail = _set_diff("archive objects", set(truth.archive), set(world.raw_objects))
    versions, delete_markers = world.raw_versions
    rewritten = {key: count for key, count in versions.items() if count != 1}
    if rewritten or delete_markers:
        return (
            False,
            f"{len(rewritten)} keys with more than one version: {_examples(rewritten)}; "
            f"{delete_markers} delete markers",
        )
    duplicates = sum(1 for d in truth.accepted) - len(truth.archive)
    return ok, detail + f", one version each, {duplicates} re-deliveries absorbed"


@invariant("no rejected request is in the archive")
def rejected_absent(truth: Truth, world: World) -> Result:
    stored = {sha256_hex(body) for body in world.raw_objects.values()} | {
        key.rsplit("/", 1)[1][:64] for key in world.raw_objects
    }
    leaked = truth.rejected_digests & stored
    rejected = sum(1 for d in truth.deliveries if d["expected_status"] != 202)
    return (
        not leaked,
        f"{rejected} rejected requests, {len(truth.rejected_digests)} distinct bodies, "
        f"{len(leaked)} found in the archive",
    )


@invariant("archived objects carry digest, signature headers and SSE-KMS under the raw key")
def archive_metadata(truth: Truth, world: World) -> Result:
    bad = []
    for key, head in world.raw_heads.items():
        metadata = head.get("Metadata", {})
        try:
            headers = json.loads(base64.b64decode(metadata["signature-headers"]))
            signed = any("signature" in name for name in headers)
        except (KeyError, ValueError):
            signed = False
        if (
            metadata.get("sha256") != sha256_hex(world.raw_objects[key])
            or not signed
            or head.get("ServerSideEncryption") != "aws:kms"
            or head.get("SSEKMSKeyId") != world.out["raw_kms_key_arn"]
        ):
            bad.append(key)
    return not bad, f"{len(world.raw_heads)} objects checked" + (f"; {len(bad)} wrong: {_examples(bad)}" if bad else "")


# --- process ----------------------------------------------------------------------


@invariant("the ingest queue is empty")
def queue_drained(truth: Truth, world: World) -> Result:
    visible, in_flight = world.queue_counts(world.out["queue_url"])
    return visible == 0 and in_flight == 0, f"{visible} visible, {in_flight} in flight"


@invariant("state table equals the state computed from ground truth")
def state(truth: Truth, world: World) -> Result:
    expected, actual = truth.state, world.state_items
    ok, detail = _set_diff("entities", set(expected), set(actual))
    wrong = []
    for key in set(expected) & set(actual):
        item = {name: actual[key].get(name) for name in expected[key]}
        if item != expected[key]:
            fields = [name for name in expected[key] if item[name] != expected[key][name]]
            wrong.append(f"{key} ({', '.join(fields)})")
    if wrong:
        return False, f"{len(wrong)} entities differ: {_examples(wrong)}"
    return ok, detail + f", every attribute equal ({len(truth.events)} events folded)"


@invariant("idempotency ledger holds exactly the genuine events")
def ledger(truth: Truth, world: World) -> Result:
    expected = {f"{vendor}#{event_id}" for vendor, event_id in truth.events}
    forbidden = {f"{vendor}#{event_id}" for vendor, event_id in truth.forbidden_event_ids} & set(world.ledger_items)
    ok, detail = _set_diff("ledger records", expected, set(world.ledger_items))
    if forbidden:
        return False, f"{len(forbidden)} rejected or unprocessable events were recorded: {_examples(forbidden)}"
    outcomes = Counter(item["outcome"] for item in world.ledger_items.values())
    return ok, detail + f" ({', '.join(f'{count} {name}' for name, count in sorted(outcomes.items()))})"


@invariant("the DLQ contains exactly the poison messages")
def dlq(truth: Truth, world: World) -> Result:
    replays = sandbox.read_json("replays.json")["count"] if (sandbox.RUN_DIR / "replays.json").exists() else 0
    keys = Counter(json.loads(message["Body"]).get("key") for message in world.dlq_messages)
    expected = Counter(dict.fromkeys(truth.poison_keys, 1 + replays))
    detail = (
        f"{sum(keys.values())} messages for {len(keys)} payloads; expected {len(expected)} payloads x {1 + replays}"
    )
    return keys == expected, detail


@invariant("quarantine holds exactly the unparseable and unknown payloads")
def quarantine(truth: Truth, world: World) -> Result:
    ok, detail = _set_diff("quarantine pointers", truth.quarantine, set(world.quarantine_objects))
    reasons = Counter(key.split("/")[2].split("=", 1)[1] for key in world.quarantine_objects)
    return ok, detail + f" ({', '.join(f'{count} {name}' for name, count in sorted(reasons.items()))})"


# --- curate -----------------------------------------------------------------------


@invariant("landing zone covers every genuine event and no other")
def landing(truth: Truth, world: World) -> Result:
    landed = {(record["vendor"], record["event_id"]) for _, record in world.landing_records}
    ok, detail = _set_diff("distinct events", set(truth.events), landed)
    misplaced = [key for key, record in world.landing_records if f"/dt={record['occurred_at'][:10]}/" not in key]
    if misplaced:
        return False, f"{len(misplaced)} records in the wrong partition: {_examples(misplaced)}"
    return (
        ok,
        detail
        + f" in {len(world.landing_objects)} objects ({len(world.landing_records)} records including duplicates)",
    )


@invariant("compacted Parquet has one row per genuine event, content equal")
def compacted(truth: Truth, world: World) -> Result:
    rows = world.compacted_rows
    by_key = {(row["vendor"], row["event_id"]): row for row in rows}
    ok, detail = _set_diff("events", set(truth.events), set(by_key))
    if len(rows) != len(by_key):
        return False, f"{len(rows) - len(by_key)} duplicate rows in the compacted table"
    wrong = []
    for key in set(truth.events) & set(by_key):
        want, row = truth.canonical(truth.events[key]), by_key[key]
        got = {
            "last_event_type": row["event_type"],
            "entity_type": row["entity_type"],
            "entity_id": row["entity_id"],
            "loan_application_id": row["loan_application_id"],
            "occurred_at": row["occurred_at"].isoformat(timespec="milliseconds") + "Z",
            "version": row["version"],
            "status": row["status"],
            "attributes": json.loads(row["attributes_json"]),
        }
        if any(got[name] != want[name] for name in got) or row["dt"] != want["occurred_at"][:10]:
            wrong.append(f"{key[0]}#{key[1]}")
    if wrong:
        return False, f"{len(wrong)} rows differ from ground truth: {_examples(wrong)}"
    return ok, detail + f" in {len(world.compacted_objects)} Parquet files, every column equal"


@invariant("Glue catalog has every compacted partition registered")
def catalog(truth: Truth, world: World) -> Result:
    in_s3 = {
        (key.split("/")[2].split("=", 1)[1], key.split("/")[3].split("=", 1)[1]) for key in world.compacted_objects
    }
    return _set_diff("partitions", in_s3, world.glue_partitions)


@invariant("Athena counts through the Glue catalog reconcile with accepted unique events")
def athena(truth: Truth, world: World) -> Result:
    expected = dict(Counter(vendor for vendor, _ in truth.events))
    rows = world.athena("SELECT vendor, count(*) AS n, count(DISTINCT event_id) AS d FROM events GROUP BY vendor")
    counted = {str(r["vendor"]): int(r["n"] or 0) for r in rows}
    distinct = {str(r["vendor"]): int(r["d"] or 0) for r in rows}
    ok = counted == expected and distinct == expected
    return (
        ok,
        f"expected {dict(sorted(expected.items()))}; "
        f"the events table, counted by Athena, has {dict(sorted(counted.items()))}",
    )


@invariant("the scheduled curation pipeline ran after the queue drained and succeeded")
def pipeline(truth: Truth, world: World) -> Result:
    settled = sandbox.read_json("settle.json")
    drained_at = datetime.fromisoformat(settled["drained_at"])
    later = [e for e in world.executions() if e["startDate"] > drained_at and e["status"] != "RUNNING"]
    if not later:
        return False, "no completed execution started after the queue drained"
    failed = [e["name"] for e in later if e["status"] != "SUCCEEDED"]
    states = world.execution_states(later[-1]["executionArn"])
    reports = sorted(world.list_keys(world.out["curated_bucket"], "dq-results/"))
    report = (
        json.loads(world.get(world.out["curated_bucket"], reports[-1])) if reports else {"passed": False, "checks": []}
    )
    violations = sum(check["violations"] for check in report["checks"])
    ok = (
        not failed
        and {"Compact", "UpdateCatalog", "DataQuality", "Reconcile", "Done"} <= set(states)
        and report["passed"]
    )
    return ok, (
        f"{len(later)} execution(s) since drain, {len(failed)} failed; states {' > '.join(states)}; "
        f"{len(report['checks'])} data-quality checks, {violations} violations"
    )


# --- security and observability ---------------------------------------------------


@invariant("no raw PII outside the raw bucket")
def pii(truth: Truth, world: World) -> Result:
    haystacks = {
        "state table": json.dumps(list(world.state_items.values()), default=str, ensure_ascii=False),
        "ledger": json.dumps(list(world.ledger_items.values()), default=str, ensure_ascii=False),
        "landing": json.dumps([record for _, record in world.landing_records], ensure_ascii=False),
        "compacted": json.dumps(world.compacted_rows, default=str, ensure_ascii=False),
        "quarantine": "\n".join(body.decode() for body in world.quarantine_objects.values()),
        "dlq": "\n".join(message["Body"] for message in world.dlq_messages),
        "logs": "\n".join(world.log_text.values()),
    }
    names, births = truth.raw_pii["names"], truth.raw_pii["dates_of_birth"]
    found = []
    for place, text in haystacks.items():
        # Structured stores hold attributes as JSON inside JSON; check both spellings.
        plain = text.replace('\\"', '"')
        hits = len(_PHONE.findall(plain)) + sum(plain.count(name) for name in names)
        hits += len(set(_DATE.findall(plain)) & births) + sum(plain.count(field) for field in _RAW_FIELDS)
        if hits:
            found.append(f"{place}: {hits}")
    size = sum(len(text) for text in haystacks.values())
    return not found, f"{size:,} characters searched in {len(haystacks)} places" + (
        f"; hits: {'; '.join(found)}" if found else ""
    )


@invariant("no secret value in logs, function configuration or queues")
def secrets_hidden(truth: Truth, world: World) -> Result:
    lam = sandbox.client("lambda")
    places = {f"{role} logs": text for role, text in world.log_text.items()}
    for role, name in world.out["function_names"].items():
        config = lam.get_function_configuration(FunctionName=name)
        places[f"{role} environment"] = json.dumps(config.get("Environment", {}))
    places["dlq"] = "\n".join(message["Body"] for message in world.dlq_messages)
    leaked = [place for place, text in places.items() if any(value in text for value in world.secret_values)]
    return not leaked, f"{len(world.secret_values)} secret values, {len(places)} places searched" + (
        f"; leaked in: {', '.join(leaked)}" if leaked else ""
    )


@invariant("the DLQ alarm fired and the notification reached a subscriber")
def alarm(truth: Truth, world: World) -> Result:
    cloudwatch = sandbox.client("cloudwatch")
    name = world.out["dlq_alarm_name"]
    deadline = time.monotonic() + 150
    state_value = "?"
    while time.monotonic() < deadline:
        alarms = cloudwatch.describe_alarms(AlarmNames=[name])["MetricAlarms"]
        state_value = alarms[0]["StateValue"] if alarms else "missing"
        if state_value == "ALARM":
            break
        time.sleep(5)
    notified = 0
    for message in world.peek(world.out["alert_sink_queue_url"], hide_seconds=5):
        try:
            body = json.loads(message["Body"])
        except ValueError:
            continue
        if body.get("AlarmName") == name and body.get("NewStateValue") == "ALARM":
            notified += 1
    return (
        state_value == "ALARM" and notified > 0,
        f"alarm state {state_value}, {notified} ALARM notification(s) delivered via SNS",
    )


def run(selected: list[str] | None = None) -> dict[str, Result]:
    truth, world = Truth.load(), World()
    results: dict[str, Result] = {}
    for name, check in CHECKS:
        if selected and not any(term in name for term in selected):
            continue
        try:
            results[name] = check(truth, world)
        except Exception as error:  # a check that cannot run has not passed
            results[name] = (False, f"check raised {type(error).__name__}: {str(error)[:200]}")
    return results


def report(results: dict[str, Result]) -> int:
    width = max(len(name) for name in results)
    for name, (ok, detail) in results.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}")
    failed = [name for name, (ok, _) in results.items() if not ok]
    print()
    print(
        f"{len(results) - len(failed)} of {len(results)} invariants hold"
        + (f"; FAILED: {'; '.join(failed)}" if failed else "")
    )
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--only", action="append", help="run only invariants whose name contains this text (repeatable)"
    )
    args = parser.parse_args()
    started = time.perf_counter()
    results = run(args.only)
    status = report(results)
    sandbox.write_json("verify.json", {name: {"ok": ok, "detail": detail} for name, (ok, detail) in results.items()})
    print(f"verified in {time.perf_counter() - started:.1f}s")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
