"""Shared permission tagger — wraps PermissionClient with S3-tag helpers.

Used by daily_sync.py, textract_trigger.py, and operational scripts to
translate DynamoDB permission mappings into S3 object tags or JSON metadata
for embedding into digital twins / chunks.
"""

from __future__ import annotations

import logging
from typing import Any

# Support both Lambda (src on PYTHONPATH) and script (lib at repo root) imports.
try:
    from lib.dynamo_permissions.client import PermissionClient
except ImportError:
    from dynamo_permissions.client import PermissionClient  # type: ignore[no-redef]

logger = logging.getLogger(__name__)


class PermissionTagger:
    """Convenience layer over :class:`PermissionClient` for tagging S3 objects.

    Parameters
    ----------
    permission_table_name:
        DynamoDB table name for ``doc-permission-mappings``.
        Falls through to ``PermissionClient`` defaults / env vars when *None*.
    dynamodb_resource:
        Optional pre-configured ``boto3.resource('dynamodb')``.
    """

    def __init__(
        self,
        permission_table_name: str | None = None,
        dynamodb_resource: Any | None = None,
    ) -> None:
        self._client = PermissionClient(
            permission_table_name=permission_table_name,
            dynamodb_resource=dynamodb_resource,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_permission_tags(self, s3_key: str) -> dict[str, str] | None:
        """Return S3-tag-formatted permission dict for *s3_key*.

        Keys returned (all string values):
        - ``allowed_groups``: comma-separated group IDs
        - ``sensitivity_level``: e.g. ``"confidential"``
        - ``matched_prefix``: the DynamoDB prefix that matched
        - ``custom_filters`` *(only if non-empty)*: ``key=value`` pairs,
          comma-separated

        Returns ``None`` when no mapping exists (quarantine signal).
        """
        perm = self._client.get_allowed_groups(s3_key)
        if perm is None:
            return None

        tags: dict[str, str] = {
            "allowed_groups": ",".join(perm.allowed_groups),
            "sensitivity_level": perm.sensitivity_level,
            "matched_prefix": perm.s3_prefix,
        }

        if perm.custom_filters:
            tags["custom_filters"] = ",".join(
                f"{k}={v}" for k, v in perm.custom_filters.items()
            )

        return tags

    def get_permission_metadata(self, s3_key: str) -> dict | None:
        """Return full permission metadata with native Python types.

        Intended for JSON embedding into digital-twin and chunk records.

        Keys returned:
        - ``allowed_groups``: ``list[str]``
        - ``sensitivity_level``: ``str``
        - ``s3_prefix``: ``str``
        - ``custom_filters``: ``dict``

        Returns ``None`` when no mapping exists.
        """
        perm = self._client.get_allowed_groups(s3_key)
        if perm is None:
            return None

        return {
            "allowed_groups": list(perm.allowed_groups),
            "sensitivity_level": perm.sensitivity_level,
            "s3_prefix": perm.s3_prefix,
            "custom_filters": dict(perm.custom_filters),
        }
