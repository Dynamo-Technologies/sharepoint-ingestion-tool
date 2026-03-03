"""Resolve a user's full group list from SAML assertion + DynamoDB cache.

Merges groups from the SAML assertion (passed at query time) with cached
groups from the ``user-group-cache`` DynamoDB table (synced via SCIM).
Deduplicates and returns a unified result with user metadata.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from lib.dynamo_permissions.client import PermissionClient

logger = logging.getLogger(__name__)


@dataclass
class ResolvedUser:
    """Result of resolving a user's groups and metadata."""

    user_id: str
    groups: list[str]
    upn: str = ""
    custom_attributes: dict[str, str] = field(default_factory=dict)
    sensitivity_ceiling: str = "internal"
    cache_hit: bool = False
    cache_expired: bool = False


class GroupResolver:
    """Merges SAML assertion groups with DynamoDB-cached groups."""

    def __init__(self, permission_client: PermissionClient | None = None) -> None:
        self._client = permission_client or PermissionClient()

    def resolve(
        self,
        user_id: str,
        saml_groups: list[str] | None = None,
    ) -> ResolvedUser:
        """Resolve the user's full group list.

        Parameters
        ----------
        user_id:
            Entra ID User Object ID.
        saml_groups:
            Group Object IDs from the SAML assertion.  May be ``None``
            or empty if the assertion was not available.

        Returns
        -------
        ResolvedUser
            Merged, deduplicated group list with user metadata.
        """
        saml_groups = saml_groups or []

        # Look up cached groups
        cache_result = self._client.get_user_groups(user_id)

        # Merge and deduplicate
        all_groups = list(set(saml_groups) | set(cache_result.groups))

        # Get sensitivity ceiling
        ceiling = self._client.get_user_sensitivity_ceiling(user_id)

        return ResolvedUser(
            user_id=user_id,
            groups=sorted(all_groups),
            upn=cache_result.upn,
            custom_attributes=dict(cache_result.custom_attributes),
            sensitivity_ceiling=ceiling,
            cache_hit=cache_result.cache_hit,
            cache_expired=cache_result.cache_expired,
        )
