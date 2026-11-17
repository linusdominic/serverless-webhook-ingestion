"""The load generator's plan: deterministic, and containing every failure mode."""

from __future__ import annotations

import ast
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest

from webhook_tools import plan

BASE = datetime(2026, 11, 17, 1, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def deliveries() -> list[plan.Delivery]:
    return plan.build(20261117, 600, BASE)


def test_same_inputs_same_plan(deliveries):
    again = plan.build(20261117, 600, BASE)
    assert plan.fingerprint(again) == plan.fingerprint(deliveries)
    assert [d.body for d in again] == [d.body for d in deliveries]


def test_different_seed_different_plan(deliveries):
    assert plan.fingerprint(plan.build(1, 600, BASE)) != plan.fingerprint(deliveries)


def test_every_failure_mode_is_present(deliveries):
    categories = Counter(d.category for d in deliveries)
    assert categories["valid"] >= 600
    for category in (
        "duplicate_exact",
        "duplicate_resigned",
        "invalid_signature",
        "stale_timestamp",
        "malformed_json",
        "unknown_event_type",
        "schema_invalid",
        "unknown_vendor",
        "body_too_large",
    ):
        assert categories[category] >= 1, category
    assert categories["poison"] == 3
    assert {d.signing for d in deliveries if d.category == "invalid_signature"} == {
        "tampered",
        "wrong_secret",
        "missing_signature",
        "malformed_signature",
        "unknown_key_id",
    }
    assert {d.signing for d in deliveries if d.category == "stale_timestamp"} == {"stale", "future"}
    assert any(d.signing == "previous_key" for d in deliveries)


def test_expected_statuses(deliveries):
    by_category = {d.category: d.expected_status for d in deliveries}
    assert by_category["invalid_signature"] == by_category["stale_timestamp"] == 401
    assert by_category["unknown_vendor"] == 404 and by_category["body_too_large"] == 413
    for accepted in (
        "valid",
        "duplicate_exact",
        "duplicate_resigned",
        "malformed_json",
        "unknown_event_type",
        "schema_invalid",
        "poison",
    ):
        assert by_category[accepted] == 202


def test_out_of_order_delivery_is_deliberate(deliveries):
    assert plan.reordered_entities(deliveries) >= 20


def test_version_ties_exist(deliveries):
    """Two events for one entity with the same version: the tie-break is exercised."""
    versions = Counter((d.event["entity_id"], d.event["version"]) for d in deliveries if d.category == "valid")
    assert any(count > 1 for count in versions.values())


def test_exact_duplicates_point_at_an_earlier_identical_body(deliveries):
    duplicates = [d for d in deliveries if d.category == "duplicate_exact"]
    assert duplicates
    for duplicate in duplicates:
        original = deliveries[duplicate.reuse_of]
        assert original.body == duplicate.body and original.index < duplicate.index and original.category == "valid"


def test_rejected_deliveries_would_corrupt_state_if_accepted(deliveries):
    """Every rejected event claims a higher version than any genuine event of its entity."""
    highest: dict[str, int] = {}
    for d in deliveries:
        if d.category == "valid":
            highest[d.event["entity_id"]] = max(highest.get(d.event["entity_id"], 0), d.event["version"])
    rejected = [
        d for d in deliveries if d.expect == "rejected" and d.event is not None and d.category != "unknown_vendor"
    ]
    assert rejected
    assert all(d.event["version"] > highest[d.event["entity_id"]] for d in rejected)


def test_indices_are_positions(deliveries):
    assert [d.index for d in deliveries] == list(range(len(deliveries)))


def test_too_few_events_is_refused():
    with pytest.raises(ValueError, match="at least 30"):
        plan.build(1, 10, BASE)


def test_tools_never_import_the_platform():
    """The generator and the verifier are an independent implementation of the vendor
    specifications. If they imported platform code, a bug there could verify itself."""
    tools = Path(__file__).resolve().parents[2] / "src" / "webhook_tools"
    offenders = []
    for path in sorted(tools.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            offenders += [f"{path.name}: {name}" for name in names if name.split(".")[0] == "webhook_platform"]
    assert offenders == []
