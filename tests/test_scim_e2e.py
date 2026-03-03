"""End-to-end simulation: SCIM sync → cache refresh → query middleware."""

from __future__ import annotations

import json
import os
import time

import boto3
import moto
import pytest
from unittest.mock import MagicMock, patch

CACHE_TABLE = "test-user-group-cache"
PERM_TABLE = "test-doc-permission-mappings"
STORE_ID = "d-e2e-test"


@pytest.fixture
def _env(monkeypatch):
    monkeypatch.setenv("IDENTITY_STORE_ID", STORE_ID)
    monkeypatch.setenv("USER_GROUP_CACHE_TABLE", CACHE_TABLE)
    monkeypatch.setenv("PERMISSION_MAPPINGS_TABLE", PERM_TABLE)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_REGION_NAME", "us-east-1")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")


@pytest.fixture
def aws(_env):
    with moto.mock_aws():
        region = "us-east-1"
        dynamodb = boto3.resource("dynamodb", region_name=region)

        dynamodb.create_table(
            TableName=CACHE_TABLE,
            KeySchema=[{"AttributeName": "user_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "user_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        dynamodb.create_table(
            TableName=PERM_TABLE,
            KeySchema=[{"AttributeName": "s3_prefix", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "s3_prefix", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # Seed permission mapping: HR docs require grp-hr
        dynamodb.Table(PERM_TABLE).put_item(Item={
            "s3_prefix": "source/Dynamo/HR",
            "allowed_groups": ["grp-hr"],
            "sensitivity_level": "confidential",
        })

        yield dynamodb


class TestScimEndToEnd:
    @patch("group_cache_refresh.GroupFlattener")
    @patch("group_cache_refresh.IdentityStoreClient")
    def test_group_change_flows_to_cache(self, MockClient, MockFlattener, aws):
        """Simulate: user gains grp-hr → cache refresh → cache has grp-hr."""
        # Phase 1: Initial sync — user has grp-all only
        mock_client = MockClient.return_value
        mock_client.list_users.return_value = iter([
            {"UserId": "alice", "UserName": "alice@dynamo.works"},
        ])
        mock_flattener = MockFlattener.return_value
        mock_flattener.flatten_all.return_value = {"alice": {"grp-all"}}

        from group_cache_refresh import handler as refresh
        refresh({}, None)

        item = aws.Table(CACHE_TABLE).get_item(Key={"user_id": "alice"})["Item"]
        assert item["groups"] == ["grp-all"]

        # Phase 2: User added to grp-hr in Identity Store
        mock_client.list_users.return_value = iter([
            {"UserId": "alice", "UserName": "alice@dynamo.works"},
        ])
        mock_flattener.flatten_all.return_value = {"alice": {"grp-all", "grp-hr"}}

        refresh({}, None)

        item = aws.Table(CACHE_TABLE).get_item(Key={"user_id": "alice"})["Item"]
        assert set(item["groups"]) == {"grp-all", "grp-hr"}

    @patch("stale_account_cleanup.IdentityStoreClient")
    @patch("group_cache_refresh.GroupFlattener")
    @patch("group_cache_refresh.IdentityStoreClient")
    def test_user_deletion_flow(
        self, MockRefreshClient, MockFlattener, MockCleanupClient, aws,
    ):
        """Simulate: user synced → deleted from Entra → cleanup marks deleted."""
        # Phase 1: Sync user
        MockRefreshClient.return_value.list_users.return_value = iter([
            {"UserId": "bob", "UserName": "bob@dynamo.works"},
        ])
        MockFlattener.return_value.flatten_all.return_value = {"bob": {"grp-all"}}

        from group_cache_refresh import handler as refresh
        refresh({}, None)

        item = aws.Table(CACHE_TABLE).get_item(Key={"user_id": "bob"})["Item"]
        assert item["groups"] == ["grp-all"]

        # Phase 2: User deleted from Identity Store
        MockCleanupClient.return_value.describe_user.return_value = None

        from stale_account_cleanup import handler as cleanup
        cleanup({}, None)

        item = aws.Table(CACHE_TABLE).get_item(Key={"user_id": "bob"})["Item"]
        assert item["status"] == "deleted"
        assert item["ttl_expiry"] > int(time.time()) + (89 * 86400)

    @patch("stale_account_cleanup.IdentityStoreClient")
    @patch("group_cache_refresh.GroupFlattener")
    @patch("group_cache_refresh.IdentityStoreClient")
    def test_user_disabled_flow(
        self, MockRefreshClient, MockFlattener, MockCleanupClient, aws,
    ):
        """Simulate: user synced → disabled (no groups) → cleanup empties groups."""
        # Phase 1: Sync user with groups
        MockRefreshClient.return_value.list_users.return_value = iter([
            {"UserId": "carol", "UserName": "carol@dynamo.works"},
        ])
        MockFlattener.return_value.flatten_all.return_value = {"carol": {"grp-hr"}}

        from group_cache_refresh import handler as refresh
        refresh({}, None)

        # Phase 2: User exists but has no group memberships
        MockCleanupClient.return_value.describe_user.return_value = {
            "UserId": "carol", "UserName": "carol@dynamo.works",
        }
        MockCleanupClient.return_value.list_group_memberships_for_member.return_value = iter([])

        from stale_account_cleanup import handler as cleanup
        cleanup({}, None)

        item = aws.Table(CACHE_TABLE).get_item(Key={"user_id": "carol"})["Item"]
        assert item["groups"] == []
        assert item["status"] == "disabled"
