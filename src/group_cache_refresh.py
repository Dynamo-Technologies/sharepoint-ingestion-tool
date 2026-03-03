"""group-cache-refresh Lambda — sync Identity Store groups to DynamoDB cache.

Triggered by EventBridge every 15 minutes. Reads all users and group
memberships from IAM Identity Center, flattens nested groups, and
writes the result to the user-group-cache DynamoDB table.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone

import boto3

try:
    from lib.identity_store.client import IdentityStoreClient
    from lib.identity_store.group_flattener import GroupFlattener
except ImportError:
    from identity_store.client import IdentityStoreClient  # type: ignore[no-redef]
    from identity_store.group_flattener import GroupFlattener  # type: ignore[no-redef]

logger = logging.getLogger(__name__)
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))


def handler(event: dict, context: object) -> dict:
    """EventBridge-triggered handler: refresh user-group cache from Identity Store."""
    identity_store_id = os.environ["IDENTITY_STORE_ID"]
    cache_table_name = os.getenv("USER_GROUP_CACHE_TABLE", "user-group-cache")
    region = os.getenv("AWS_REGION_NAME", os.getenv("AWS_REGION", "us-east-1"))

    id_client = IdentityStoreClient(identity_store_id=identity_store_id)
    flattener = GroupFlattener(id_client)

    users = {u["UserId"]: u for u in id_client.list_users()}
    user_groups = flattener.flatten_all()

    dynamodb = boto3.resource("dynamodb", region_name=region)
    table = dynamodb.Table(cache_table_name)

    stats = {"updated": 0, "unchanged": 0, "errors": 0}

    for user_id, groups in user_groups.items():
        try:
            user = users.get(user_id, {})
            upn = user.get("UserName", "")

            existing = table.get_item(Key={"user_id": user_id}).get("Item")
            existing_groups = set(existing.get("groups", [])) if existing else set()

            if groups == existing_groups:
                stats["unchanged"] += 1
                continue

            added = groups - existing_groups
            removed = existing_groups - groups
            if added:
                logger.info("User %s (%s): added groups %s", user_id, upn, sorted(added))
            if removed:
                logger.info("User %s (%s): removed groups %s", user_id, upn, sorted(removed))

            ttl_expiry = int(time.time()) + 86400
            item = {
                "user_id": user_id,
                "upn": upn,
                "groups": sorted(groups),
                "last_synced": datetime.now(timezone.utc).isoformat(),
                "source": "scim",
                "ttl_expiry": ttl_expiry,
            }
            if existing and existing.get("custom_attributes"):
                item["custom_attributes"] = existing["custom_attributes"]

            table.put_item(Item=item)
            stats["updated"] += 1
        except Exception:
            logger.exception("Error processing user %s", user_id)
            stats["errors"] += 1

    logger.info("Cache refresh complete: %s", stats)
    return {"statusCode": 200, "body": json.dumps(stats)}
