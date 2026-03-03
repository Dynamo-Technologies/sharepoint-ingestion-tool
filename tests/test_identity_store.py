"""Tests for IdentityStoreClient — paginated Identity Store API wrapper."""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError
from unittest.mock import MagicMock

from lib.identity_store.client import IdentityStoreClient

STORE_ID = "d-1234567890"


@pytest.fixture
def mock_boto_client():
    return MagicMock()


@pytest.fixture
def client(mock_boto_client):
    return IdentityStoreClient(
        identity_store_id=STORE_ID,
        client=mock_boto_client,
    )


class TestListUsers:
    def test_single_page(self, client, mock_boto_client):
        mock_boto_client.list_users.return_value = {
            "Users": [{"UserId": "u1", "UserName": "a@test.com"}],
        }
        result = list(client.list_users())
        assert len(result) == 1
        assert result[0]["UserId"] == "u1"
        mock_boto_client.list_users.assert_called_once_with(
            IdentityStoreId=STORE_ID,
        )

    def test_pagination(self, client, mock_boto_client):
        mock_boto_client.list_users.side_effect = [
            {"Users": [{"UserId": "u1"}], "NextToken": "tok1"},
            {"Users": [{"UserId": "u2"}]},
        ]
        result = list(client.list_users())
        assert [u["UserId"] for u in result] == ["u1", "u2"]
        assert mock_boto_client.list_users.call_count == 2

    def test_empty(self, client, mock_boto_client):
        mock_boto_client.list_users.return_value = {"Users": []}
        assert list(client.list_users()) == []


class TestListGroups:
    def test_single_page(self, client, mock_boto_client):
        mock_boto_client.list_groups.return_value = {
            "Groups": [{"GroupId": "g1", "DisplayName": "Grp1"}],
        }
        result = list(client.list_groups())
        assert len(result) == 1
        assert result[0]["GroupId"] == "g1"

    def test_empty(self, client, mock_boto_client):
        mock_boto_client.list_groups.return_value = {"Groups": []}
        assert list(client.list_groups()) == []


class TestListGroupMemberships:
    def test_returns_members(self, client, mock_boto_client):
        mock_boto_client.list_group_memberships.return_value = {
            "GroupMemberships": [
                {"MembershipId": "m1", "GroupId": "g1", "MemberId": {"UserId": "u1"}},
            ],
        }
        result = list(client.list_group_memberships("g1"))
        assert len(result) == 1
        mock_boto_client.list_group_memberships.assert_called_once_with(
            IdentityStoreId=STORE_ID, GroupId="g1",
        )

    def test_pagination(self, client, mock_boto_client):
        mock_boto_client.list_group_memberships.side_effect = [
            {
                "GroupMemberships": [{"MembershipId": "m1", "MemberId": {"UserId": "u1"}}],
                "NextToken": "tok",
            },
            {
                "GroupMemberships": [{"MembershipId": "m2", "MemberId": {"UserId": "u2"}}],
            },
        ]
        result = list(client.list_group_memberships("g1"))
        assert len(result) == 2


class TestListGroupMembershipsForMember:
    def test_returns_groups(self, client, mock_boto_client):
        mock_boto_client.list_group_memberships_for_member.return_value = {
            "GroupMemberships": [
                {"GroupId": "g1", "MemberId": {"UserId": "u1"}},
                {"GroupId": "g2", "MemberId": {"UserId": "u1"}},
            ],
        }
        result = list(client.list_group_memberships_for_member("u1"))
        assert len(result) == 2


class TestDescribeUser:
    def test_existing_user(self, client, mock_boto_client):
        mock_boto_client.describe_user.return_value = {
            "UserId": "u1", "UserName": "a@test.com",
        }
        result = client.describe_user("u1")
        assert result["UserId"] == "u1"

    def test_not_found_returns_none(self, client, mock_boto_client):
        mock_boto_client.describe_user.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "Not found"}},
            "DescribeUser",
        )
        assert client.describe_user("missing") is None

    def test_other_error_raises(self, client, mock_boto_client):
        mock_boto_client.describe_user.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Denied"}},
            "DescribeUser",
        )
        with pytest.raises(ClientError):
            client.describe_user("u1")
