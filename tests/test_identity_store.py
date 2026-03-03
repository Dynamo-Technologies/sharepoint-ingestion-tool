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


from lib.identity_store.group_flattener import GroupFlattener


def _make_flattener(groups, memberships_by_group):
    """Helper: build a GroupFlattener with a mocked IdentityStoreClient."""
    mock_client = MagicMock()
    mock_client.list_groups.return_value = iter(groups)
    mock_client.list_group_memberships.side_effect = (
        lambda gid: iter(memberships_by_group.get(gid, []))
    )
    return GroupFlattener(mock_client)


class TestGroupFlattener:
    def test_flat_groups_no_nesting(self):
        """Users directly in groups, no nesting."""
        flattener = _make_flattener(
            groups=[{"GroupId": "g1"}, {"GroupId": "g2"}],
            memberships_by_group={
                "g1": [{"MemberId": {"UserId": "u1"}}, {"MemberId": {"UserId": "u2"}}],
                "g2": [{"MemberId": {"UserId": "u2"}}],
            },
        )
        result = flattener.flatten_all()
        assert result["u1"] == {"g1"}
        assert result["u2"] == {"g1", "g2"}

    def test_two_level_nesting(self):
        """User in child group, child group in parent group."""
        flattener = _make_flattener(
            groups=[{"GroupId": "parent"}, {"GroupId": "child"}],
            memberships_by_group={
                "parent": [{"MemberId": {"GroupId": "child"}}],
                "child": [{"MemberId": {"UserId": "u1"}}],
            },
        )
        result = flattener.flatten_all()
        assert result["u1"] == {"child", "parent"}

    def test_three_level_nesting(self):
        """3-level: user -> child -> mid -> top."""
        flattener = _make_flattener(
            groups=[
                {"GroupId": "top"}, {"GroupId": "mid"}, {"GroupId": "child"},
            ],
            memberships_by_group={
                "top": [{"MemberId": {"GroupId": "mid"}}],
                "mid": [{"MemberId": {"GroupId": "child"}}],
                "child": [{"MemberId": {"UserId": "u1"}}],
            },
        )
        result = flattener.flatten_all()
        assert result["u1"] == {"child", "mid", "top"}

    def test_circular_reference(self):
        """Groups referencing each other — must not infinite loop."""
        flattener = _make_flattener(
            groups=[{"GroupId": "gA"}, {"GroupId": "gB"}],
            memberships_by_group={
                "gA": [
                    {"MemberId": {"GroupId": "gB"}},
                    {"MemberId": {"UserId": "u1"}},
                ],
                "gB": [{"MemberId": {"GroupId": "gA"}}],
            },
        )
        result = flattener.flatten_all()
        assert result["u1"] == {"gA", "gB"}

    def test_empty_groups(self):
        """No users in any group."""
        flattener = _make_flattener(
            groups=[{"GroupId": "g1"}],
            memberships_by_group={"g1": []},
        )
        assert flattener.flatten_all() == {}

    def test_user_in_multiple_nested_paths(self):
        """User reaches top group via two different paths."""
        flattener = _make_flattener(
            groups=[
                {"GroupId": "top"}, {"GroupId": "left"}, {"GroupId": "right"},
            ],
            memberships_by_group={
                "top": [
                    {"MemberId": {"GroupId": "left"}},
                    {"MemberId": {"GroupId": "right"}},
                ],
                "left": [{"MemberId": {"UserId": "u1"}}],
                "right": [{"MemberId": {"UserId": "u1"}}],
            },
        )
        result = flattener.flatten_all()
        assert result["u1"] == {"left", "right", "top"}
