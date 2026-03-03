"""DynamoDB-backed permission resolution for the RAG pipeline.

Provides functions to check document access based on Entra ID group
memberships cached in DynamoDB and S3 prefix permission mappings.
"""

from lib.dynamo_permissions.client import (
    PermissionClient,
    AccessCheckResult,
    UserGroupResult,
    PrefixPermission,
)

__all__ = [
    "PermissionClient",
    "AccessCheckResult",
    "UserGroupResult",
    "PrefixPermission",
]
