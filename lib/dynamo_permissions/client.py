"""DynamoDB permission client for RAG access control.

Reads from two DynamoDB tables:

- ``doc-permission-mappings``: Maps S3 prefixes to allowed Entra group IDs.
- ``user-group-cache``: Caches each user's flattened group membership list.

Access decisions use longest-prefix matching and group intersection logic.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key

logger = logging.getLogger(__name__)

# Sensitivity levels ordered from lowest to highest
SENSITIVITY_LEVELS = ["public", "internal", "confidential", "restricted"]


@dataclass
class PrefixPermission:
    """Permission mapping for an S3 prefix."""

    s3_prefix: str
    allowed_groups: list[str]
    sensitivity_level: str = "internal"
    custom_filters: dict[str, Any] = field(default_factory=dict)
    last_updated: str = ""
    updated_by: str = ""


@dataclass
class UserGroupResult:
    """Result of looking up a user's group memberships."""

    user_id: str
    groups: list[str]
    upn: str = ""
    custom_attributes: dict[str, str] = field(default_factory=dict)
    last_synced: str = ""
    source: str = ""
    cache_hit: bool = True
    cache_expired: bool = False


@dataclass
class AccessCheckResult:
    """Result of an access check."""

    allowed: bool
    user_id: str
    s3_prefix: str
    matched_prefix: str = ""
    matching_groups: list[str] = field(default_factory=list)
    reason: str = ""
    cache_expired: bool = False


class PermissionClient:
    """DynamoDB-backed permission resolver.

    Parameters
    ----------
    permission_table_name:
        Name of the ``doc-permission-mappings`` table.
    user_cache_table_name:
        Name of the ``user-group-cache`` table.
    dynamodb_resource:
        An optional ``boto3.resource('dynamodb')`` instance.
        Defaults to creating one from the environment.
    ttl_grace_seconds:
        Number of seconds past TTL expiry before treating the cache
        entry as truly expired.  Default 0 (strict).
    """

    def __init__(
        self,
        permission_table_name: str | None = None,
        user_cache_table_name: str | None = None,
        dynamodb_resource: Any | None = None,
        ttl_grace_seconds: int = 0,
    ) -> None:
        self._dynamo = dynamodb_resource or boto3.resource(
            "dynamodb",
            region_name=os.getenv("AWS_REGION_NAME", os.getenv("AWS_REGION", "us-east-1")),
        )
        self._perm_table = self._dynamo.Table(
            permission_table_name
            or os.getenv("PERMISSION_MAPPINGS_TABLE", "doc-permission-mappings")
        )
        self._cache_table = self._dynamo.Table(
            user_cache_table_name
            or os.getenv("USER_GROUP_CACHE_TABLE", "user-group-cache")
        )
        self._ttl_grace = ttl_grace_seconds

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_allowed_groups(self, s3_prefix: str) -> PrefixPermission | None:
        """Look up the permission mapping for an S3 prefix.

        Uses **longest prefix match**: scans all stored prefixes and
        returns the one that is the longest match for the given key.
        Returns ``None`` if no prefix matches (quarantine signal).
        """
        # Normalise: strip leading/trailing slashes for consistent matching
        s3_prefix = s3_prefix.strip("/")

        try:
            response = self._perm_table.scan()
            items = response.get("Items", [])

            # Handle pagination
            while "LastEvaluatedKey" in response:
                response = self._perm_table.scan(
                    ExclusiveStartKey=response["LastEvaluatedKey"]
                )
                items.extend(response.get("Items", []))

        except Exception:
            logger.exception("Failed to scan permission mappings table")
            return None

        # Find longest prefix match
        best_match: dict | None = None
        best_len = -1

        for item in items:
            stored_prefix = item.get("s3_prefix", "").strip("/")
            if s3_prefix.startswith(stored_prefix) and len(stored_prefix) > best_len:
                best_match = item
                best_len = len(stored_prefix)

        if best_match is None:
            logger.debug("No permission mapping found for prefix: %s", s3_prefix)
            return None

        return PrefixPermission(
            s3_prefix=best_match.get("s3_prefix", ""),
            allowed_groups=list(best_match.get("allowed_groups", [])),
            sensitivity_level=best_match.get("sensitivity_level", "internal"),
            custom_filters=dict(best_match.get("custom_filters", {})),
            last_updated=best_match.get("last_updated", ""),
            updated_by=best_match.get("updated_by", ""),
        )

    def get_user_groups(self, user_id: str) -> UserGroupResult:
        """Return the user's flattened group list from the cache.

        Sets ``cache_hit=False`` if the user is not found.
        Sets ``cache_expired=True`` if the TTL has passed.
        """
        try:
            response = self._cache_table.get_item(Key={"user_id": user_id})
        except Exception:
            logger.exception("Failed to get user groups for %s", user_id)
            return UserGroupResult(
                user_id=user_id, groups=[], cache_hit=False
            )

        item = response.get("Item")
        if not item:
            return UserGroupResult(
                user_id=user_id, groups=[], cache_hit=False
            )

        # Check TTL expiry
        ttl_expiry = int(item.get("ttl_expiry", 0))
        now = int(time.time())
        expired = ttl_expiry > 0 and now > (ttl_expiry + self._ttl_grace)

        return UserGroupResult(
            user_id=user_id,
            groups=list(item.get("groups", [])),
            upn=item.get("upn", ""),
            custom_attributes=dict(item.get("custom_attributes", {})),
            last_synced=item.get("last_synced", ""),
            source=item.get("source", ""),
            cache_hit=True,
            cache_expired=expired,
        )

    def check_access(self, user_id: str, s3_prefix: str) -> AccessCheckResult:
        """Check if a user can access documents under an S3 prefix.

        Returns ``AccessCheckResult`` with ``allowed=True`` if the
        user's groups intersect with the prefix's allowed groups.
        """
        # Look up prefix permissions
        perm = self.get_allowed_groups(s3_prefix)
        if perm is None:
            return AccessCheckResult(
                allowed=False,
                user_id=user_id,
                s3_prefix=s3_prefix,
                reason="no_mapping",
            )

        # Look up user groups
        user = self.get_user_groups(user_id)
        if not user.cache_hit:
            return AccessCheckResult(
                allowed=False,
                user_id=user_id,
                s3_prefix=s3_prefix,
                matched_prefix=perm.s3_prefix,
                reason="user_not_found",
            )

        # Check intersection
        user_group_set = set(user.groups)
        allowed_group_set = set(perm.allowed_groups)
        matching = sorted(user_group_set & allowed_group_set)

        return AccessCheckResult(
            allowed=len(matching) > 0,
            user_id=user_id,
            s3_prefix=s3_prefix,
            matched_prefix=perm.s3_prefix,
            matching_groups=matching,
            reason="authorized" if matching else "no_group_match",
            cache_expired=user.cache_expired,
        )

    def get_user_sensitivity_ceiling(self, user_id: str) -> str:
        """Return the maximum sensitivity level the user is authorized for.

        Checks the user's custom attributes:
        - ``ext_ClearanceLevel``: maps to sensitivity levels
        - ``ext_DataSensitivity``: direct sensitivity override

        Falls back to ``"internal"`` if no custom attributes are set.
        """
        user = self.get_user_groups(user_id)
        if not user.cache_hit:
            return "public"

        attrs = user.custom_attributes

        # Direct sensitivity override takes precedence
        direct = attrs.get("ext_DataSensitivity", "").lower()
        if direct in SENSITIVITY_LEVELS:
            return direct

        # Map clearance level to sensitivity
        clearance = attrs.get("ext_ClearanceLevel", "").lower()
        clearance_map = {
            "public": "public",
            "none": "internal",
            "internal": "internal",
            "confidential": "confidential",
            "secret": "restricted",
            "topsecret": "restricted",
            "top_secret": "restricted",
        }
        if clearance in clearance_map:
            return clearance_map[clearance]

        # Default: internal access for cached users
        return "internal"

    # ------------------------------------------------------------------
    # Admin / Seeding helpers
    # ------------------------------------------------------------------

    def put_permission_mapping(self, mapping: PrefixPermission) -> None:
        """Write a single permission mapping to DynamoDB."""
        item: dict[str, Any] = {
            "s3_prefix": mapping.s3_prefix,
            "allowed_groups": mapping.allowed_groups,
            "sensitivity_level": mapping.sensitivity_level,
            "last_updated": mapping.last_updated,
            "updated_by": mapping.updated_by,
        }
        if mapping.custom_filters:
            item["custom_filters"] = mapping.custom_filters

        self._perm_table.put_item(Item=item)

    def put_user_cache(
        self,
        user_id: str,
        upn: str,
        groups: list[str],
        custom_attributes: dict[str, str] | None = None,
        source: str = "seed",
        ttl_hours: int = 24,
    ) -> None:
        """Write a single user-group cache entry to DynamoDB."""
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        ttl_expiry = int(now.timestamp()) + (ttl_hours * 3600)

        item: dict[str, Any] = {
            "user_id": user_id,
            "upn": upn,
            "groups": groups,
            "custom_attributes": custom_attributes or {},
            "last_synced": now.isoformat(),
            "source": source,
            "ttl_expiry": ttl_expiry,
        }
        self._cache_table.put_item(Item=item)
