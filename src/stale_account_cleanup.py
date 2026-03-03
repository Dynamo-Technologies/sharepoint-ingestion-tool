"""stale-account-cleanup Lambda — detect and handle deprovisioned users.

Triggered by EventBridge daily (03:00 UTC). Scans the user-group-cache
table and validates each user against the Identity Store. Users deleted
from Entra ID get a 90-day TTL; disabled users have their groups emptied.
"""

from __future__ import annotations

import json
import logging
import os
import time

import boto3

try:
    from lib.identity_store.client import IdentityStoreClient
except ImportError:
    from identity_store.client import IdentityStoreClient  # type: ignore[no-redef]

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))

_90_DAYS_SECONDS = 90 * 86400


def handler(event: dict, context: object) -> dict:
    """EventBridge-triggered handler: clean up stale user accounts."""
    identity_store_id = os.environ["IDENTITY_STORE_ID"]
    cache_table_name = os.getenv("USER_GROUP_CACHE_TABLE", "user-group-cache")
    region = os.getenv("AWS_REGION_NAME", os.getenv("AWS_REGION", "us-east-1"))

    id_client = IdentityStoreClient(identity_store_id=identity_store_id)
    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(cache_table_name)

    stats = {"active": 0, "disabled": 0, "deleted": 0, "errors": 0}

    items = _scan_all(table)
    active_items = [i for i in items if i.get("status") != "deleted"]

    for item in active_items:
        user_id = item["user_id"]
        try:
            user = id_client.describe_user(user_id)
            if user is None:
                _mark_deleted(table, user_id)
                stats["deleted"] += 1
                logger.info("Marked user %s as deleted (not in Identity Store)", user_id)
            else:
                memberships = list(id_client.list_group_memberships_for_member(user_id))
                previous_groups = item.get("groups", [])
                if not memberships and previous_groups:
                    _mark_disabled(table, user_id)
                    stats["disabled"] += 1
                    logger.info("Disabled user %s (no group memberships)", user_id)
                else:
                    stats["active"] += 1
        except Exception:
            logger.exception("Error checking user %s", user_id)
            stats["errors"] += 1

    logger.info("Stale account cleanup complete: %s", stats)
    return {"statusCode": 200, "body": json.dumps(stats)}


def _mark_deleted(table, user_id: str) -> None:
    ttl_expiry = int(time.time()) + _90_DAYS_SECONDS
    table.update_item(
        Key={"user_id": user_id},
        UpdateExpression="SET #s = :s, ttl_expiry = :ttl",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":s": "deleted", ":ttl": ttl_expiry},
    )


def _mark_disabled(table, user_id: str) -> None:
    table.update_item(
        Key={"user_id": user_id},
        UpdateExpression="SET groups = :g, #s = :s",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":g": [], ":s": "disabled"},
    )


def _scan_all(table) -> list[dict]:
    items: list[dict] = []
    kwargs: dict = {}
    while True:
        response = table.scan(**kwargs)
        items.extend(response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    return items
