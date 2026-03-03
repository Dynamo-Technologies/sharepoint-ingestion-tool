"""Tests for stale-account-cleanup Lambda handler."""

from __future__ import annotations

import json
import os
import time

import boto3
import moto
import pytest
from unittest.mock import MagicMock, patch

CACHE_TABLE = "test-user-group-cache"
STORE_ID = "d-test123"


@pytest.fixture
def _env(monkeypatch):
    monkeypatch.setenv("IDENTITY_STORE_ID", STORE_ID)
    monkeypatch.setenv("USER_GROUP_CACHE_TABLE", CACHE_TABLE)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_REGION_NAME", "us-east-1")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")


@pytest.fixture
def dynamodb_table(_env):
    with moto.mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        dynamodb.create_table(
            TableName=CACHE_TABLE,
            KeySchema=[{"AttributeName": "user_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "user_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield dynamodb


def _seed_user(dynamodb, user_id, groups, status=None):
    item = {
        "user_id": user_id,
        "upn": f"{user_id}@test.com",
        "groups": groups,
        "last_synced": "2026-01-01T00:00:00Z",
        "source": "scim",
        "ttl_expiry": int(time.time()) + 86400,
    }
    if status:
        item["status"] = status
    dynamodb.Table(CACHE_TABLE).put_item(Item=item)


class TestStaleAccountCleanup:
    @patch("stale_account_cleanup.IdentityStoreClient")
    def test_active_user_unchanged(self, MockClient, dynamodb_table):
        _seed_user(dynamodb_table, "u1", ["g1", "g2"])

        mock_inst = MockClient.return_value
        mock_inst.describe_user.return_value = {"UserId": "u1", "UserName": "u1@test.com"}
        mock_inst.list_group_memberships_for_member.return_value = iter([
            {"GroupId": "g1", "MemberId": {"UserId": "u1"}},
        ])

        from stale_account_cleanup import handler
        result = handler({}, None)
        body = json.loads(result["body"])

        assert body["active"] == 1
        assert body["deleted"] == 0
        assert body["disabled"] == 0

        item = dynamodb_table.Table(CACHE_TABLE).get_item(Key={"user_id": "u1"})["Item"]
        assert "status" not in item or item.get("status") != "deleted"

    @patch("stale_account_cleanup.IdentityStoreClient")
    def test_deleted_user_marked(self, MockClient, dynamodb_table):
        _seed_user(dynamodb_table, "u1", ["g1"])

        mock_inst = MockClient.return_value
        mock_inst.describe_user.return_value = None  # user not found

        from stale_account_cleanup import handler
        result = handler({}, None)
        body = json.loads(result["body"])

        assert body["deleted"] == 1

        item = dynamodb_table.Table(CACHE_TABLE).get_item(Key={"user_id": "u1"})["Item"]
        assert item["status"] == "deleted"
        assert item["ttl_expiry"] > int(time.time()) + (89 * 86400)

    @patch("stale_account_cleanup.IdentityStoreClient")
    def test_disabled_user_groups_emptied(self, MockClient, dynamodb_table):
        _seed_user(dynamodb_table, "u1", ["g1", "g2"])

        mock_inst = MockClient.return_value
        mock_inst.describe_user.return_value = {"UserId": "u1", "UserName": "u1@test.com"}
        mock_inst.list_group_memberships_for_member.return_value = iter([])

        from stale_account_cleanup import handler
        result = handler({}, None)
        body = json.loads(result["body"])

        assert body["disabled"] == 1

        item = dynamodb_table.Table(CACHE_TABLE).get_item(Key={"user_id": "u1"})["Item"]
        assert item["groups"] == []
        assert item["status"] == "disabled"

    @patch("stale_account_cleanup.IdentityStoreClient")
    def test_already_deleted_skipped(self, MockClient, dynamodb_table):
        _seed_user(dynamodb_table, "u1", ["g1"], status="deleted")

        from stale_account_cleanup import handler
        result = handler({}, None)
        body = json.loads(result["body"])

        assert body["active"] == 0
        assert body["deleted"] == 0
        assert body["disabled"] == 0

    @patch("stale_account_cleanup.IdentityStoreClient")
    def test_user_with_no_previous_groups_not_disabled(self, MockClient, dynamodb_table):
        """User with empty groups who still exists is not re-disabled."""
        _seed_user(dynamodb_table, "u1", [])

        mock_inst = MockClient.return_value
        mock_inst.describe_user.return_value = {"UserId": "u1", "UserName": "u1@test.com"}
        mock_inst.list_group_memberships_for_member.return_value = iter([])

        from stale_account_cleanup import handler
        result = handler({}, None)
        body = json.loads(result["body"])

        assert body["active"] == 1
        assert body["disabled"] == 0
