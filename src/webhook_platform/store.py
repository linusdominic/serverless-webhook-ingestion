"""DynamoDB access: entity state and the idempotency ledger."""

from __future__ import annotations

from typing import Any

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from .normalize import CanonicalEvent
from .ordering import CONDITION_EXPRESSION, Decision, decide
from .reconciliation import ingest_day_key

_serializer = TypeSerializer()
_deserializer = TypeDeserializer()


def _is_conditional_failure(error: ClientError) -> bool:
    return bool(error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException")


def apply_event(client: Any, table: str, event: CanonicalEvent, *, now_iso: str) -> Decision:
    """Move the entity's state to this event if, and only if, the event is newer.

    One conditional UpdateItem. There is no read-then-write, so two processors
    handling events for the same entity cannot interleave: DynamoDB evaluates the
    condition and applies the update atomically per item.

    Serialisation happens before the call and raises for values DynamoDB cannot
    hold (numbers beyond 38 significant digits, for one). That is left to propagate.
    """
    values = {
        ":version": event.version,
        ":event_id": event.event_id,
        ":event_type": event.event_type,
        ":status": event.status,
        ":vendor": event.vendor,
        ":entity_type": event.entity_type,
        ":entity_id": event.entity_id,
        ":loan_application_id": event.loan_application_id,
        ":occurred_at": event.occurred_at,
        ":updated_at": now_iso,
        ":attributes": event.attributes,
    }
    serialized = {name: _serializer.serialize(value) for name, value in values.items()}
    try:
        client.update_item(
            TableName=table,
            Key={"pk": {"S": event.state_key}},
            UpdateExpression=(
                "SET #version = :version, last_event_id = :event_id, last_event_type = :event_type, "
                "#status = :status, vendor = :vendor, entity_type = :entity_type, entity_id = :entity_id, "
                "loan_application_id = :loan_application_id, occurred_at = :occurred_at, "
                "updated_at = :updated_at, #attributes = :attributes"
            ),
            ConditionExpression=CONDITION_EXPRESSION,
            ExpressionAttributeNames={"#version": "version", "#status": "status", "#attributes": "attributes"},
            ExpressionAttributeValues=serialized,
            ReturnValuesOnConditionCheckFailure="ALL_OLD",
        )
        return Decision.APPLY
    except ClientError as error:
        if not _is_conditional_failure(error):
            raise
        old = error.response.get("Item")
        if not old:
            return Decision.STALE
        current = (int(_deserializer.deserialize(old["version"])), str(old["last_event_id"]["S"]))
        decision = decide(current, (event.version, event.event_id))
        # APPLY cannot happen here: the condition just failed against this item.
        return Decision.STALE if decision is Decision.APPLY else decision


def already_processed(client: Any, table: str, event: CanonicalEvent) -> bool:
    response = client.get_item(
        TableName=table,
        Key={"pk": {"S": event.idempotency_key}},
        ProjectionExpression="pk",
        ConsistentRead=True,
    )
    return "Item" in response


def mark_processed(
    client: Any,
    table: str,
    event: CanonicalEvent,
    *,
    outcome: Decision,
    ingested_at: str,
    archive_key: str,
    expires_at: int,
) -> bool:
    """Record that every effect of this event is durable. Returns False if another
    invocation recorded it first, which is harmless: the effects are idempotent."""
    try:
        client.put_item(
            TableName=table,
            Item={
                "pk": {"S": event.idempotency_key},
                "vendor": {"S": event.vendor},
                "event_id": {"S": event.event_id},
                "state_key": {"S": event.state_key},
                "outcome": {"S": outcome.value},
                "ingested_at": {"S": ingested_at},
                # Partition key of the by-ingest-day index that reconciliation reads.
                "ingest_day": {"S": ingest_day_key(event.vendor, ingested_at)},
                "archive_key": {"S": archive_key},
                "expires_at": {"N": str(expires_at)},
            },
            ConditionExpression="attribute_not_exists(pk)",
        )
        return True
    except ClientError as error:
        if _is_conditional_failure(error):
            return False
        raise
