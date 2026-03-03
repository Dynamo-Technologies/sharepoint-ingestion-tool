"""Paginated wrapper around the AWS Identity Store API."""

from __future__ import annotations

import logging
from typing import Any, Iterator

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class IdentityStoreClient:
    """Paginated Identity Store API client.

    Parameters
    ----------
    identity_store_id:
        The Identity Store ID from IAM Identity Center.
    client:
        Optional pre-configured boto3 identitystore client (for testing).
    """

    def __init__(
        self,
        identity_store_id: str,
        client: Any | None = None,
    ) -> None:
        self._identity_store_id = identity_store_id
        self._client = client or boto3.client("identitystore")

    def list_users(self) -> Iterator[dict]:
        """Yield all users from the Identity Store."""
        yield from self._paginate(
            self._client.list_users,
            "Users",
            IdentityStoreId=self._identity_store_id,
        )

    def list_groups(self) -> Iterator[dict]:
        """Yield all groups from the Identity Store."""
        yield from self._paginate(
            self._client.list_groups,
            "Groups",
            IdentityStoreId=self._identity_store_id,
        )

    def list_group_memberships(self, group_id: str) -> Iterator[dict]:
        """Yield all memberships for a group."""
        yield from self._paginate(
            self._client.list_group_memberships,
            "GroupMemberships",
            IdentityStoreId=self._identity_store_id,
            GroupId=group_id,
        )

    def list_group_memberships_for_member(self, user_id: str) -> Iterator[dict]:
        """Yield all group memberships for a user."""
        yield from self._paginate(
            self._client.list_group_memberships_for_member,
            "GroupMemberships",
            IdentityStoreId=self._identity_store_id,
            MemberId={"UserId": user_id},
        )

    def describe_user(self, user_id: str) -> dict | None:
        """Return user details, or None if user not found."""
        try:
            return self._client.describe_user(
                IdentityStoreId=self._identity_store_id,
                UserId=user_id,
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                return None
            raise

    @staticmethod
    def _paginate(method, result_key: str, **kwargs) -> Iterator[dict]:
        """Generic paginator for Identity Store APIs."""
        while True:
            response = method(**kwargs)
            yield from response.get(result_key, [])
            next_token = response.get("NextToken")
            if not next_token:
                break
            kwargs["NextToken"] = next_token
