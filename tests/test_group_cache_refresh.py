"""Tests for group-cache-refresh Lambda handler."""

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


def _seed_cache(dynamodb, user_id, groups, upn="test@test.com"):
    table = dynamodb.Table(CACHE_TABLE)
    table.put_item(Item={
        "user_id": user_id,
        "upn": upn,
        "groups": groups,
        "last_synced": "2026-01-01T00:00:00Z",
        "source": "scim",
        "ttl_expiry": int(time.time()) + 86400,
    })


class TestGroupCacheRefresh:
    @patch("group_cache_refresh.GroupFlattener")
    @patch("group_cache_refresh.IdentityStoreClient")
    def test_new_user_written_to_cache(
        self, MockClient, MockFlattener, dynamodb_table,
    ):
        mock_client_inst = MagicMock()
        mock_client_inst.list_users.return_value = iter([
            {"UserId": "u1", "UserName": "alice@test.com"},
        ])
        MockClient.return_value = mock_client_inst

        mock_flattener_inst = MagicMock()
        mock_flattener_inst.flatten_all.return_value = {"u1": {"g1", "g2"}}
        MockFlattener.return_value = mock_flattener_inst

        from group_cache_refresh import handler
        result = handler({}, None)
        body = json.loads(result["body"])

        assert result["statusCode"] == 200
        assert body["updated"] == 1

        table = dynamodb_table.Table(CACHE_TABLE)
        item = table.get_item(Key={"user_id": "u1"}).get("Item")
        assert item is not None
        assert set(item["groups"]) == {"g1", "g2"}
        assert item["upn"] == "alice@test.com"
        assert item["source"] == "scim"

    @patch("group_cache_refresh.GroupFlattener")
    @patch("group_cache_refresh.IdentityStoreClient")
    def test_unchanged_user_not_rewritten(
        self, MockClient, MockFlattener, dynamodb_table,
    ):
        _seed_cache(dynamodb_table, "u1", ["g1", "g2"])

        mock_client_inst = MagicMock()
        mock_client_inst.list_users.return_value = iter([
            {"UserId": "u1", "UserName": "alice@test.com"},
        ])
        MockClient.return_value = mock_client_inst

        mock_flattener_inst = MagicMock()
        mock_flattener_inst.flatten_all.return_value = {"u1": {"g1", "g2"}}
        MockFlattener.return_value = mock_flattener_inst

        from group_cache_refresh import handler
        result = handler({}, None)
        body = json.loads(result["body"])

        assert body["unchanged"] == 1
        assert body["updated"] == 0

    @patch("group_cache_refresh.GroupFlattener")
    @patch("group_cache_refresh.IdentityStoreClient")
    def test_group_change_detected_and_logged(
        self, MockClient, MockFlattener, dynamodb_table,
    ):
        _seed_cache(dynamodb_table, "u1", ["g1"])

        mock_client_inst = MagicMock()
        mock_client_inst.list_users.return_value = iter([
            {"UserId": "u1", "UserName": "alice@test.com"},
        ])
        MockClient.return_value = mock_client_inst

        mock_flattener_inst = MagicMock()
        mock_flattener_inst.flatten_all.return_value = {"u1": {"g1", "g2"}}
        MockFlattener.return_value = mock_flattener_inst

        from group_cache_refresh import handler
        result = handler({}, None)
        body = json.loads(result["body"])

        assert body["updated"] == 1

        table = dynamodb_table.Table(CACHE_TABLE)
        item = table.get_item(Key={"user_id": "u1"}).get("Item")
        assert set(item["groups"]) == {"g1", "g2"}

    @patch("group_cache_refresh.GroupFlattener")
    @patch("group_cache_refresh.IdentityStoreClient")
    def test_empty_identity_store(
        self, MockClient, MockFlattener, dynamodb_table,
    ):
        MockClient.return_value.list_users.return_value = iter([])
        MockFlattener.return_value.flatten_all.return_value = {}

        from group_cache_refresh import handler
        result = handler({}, None)
        body = json.loads(result["body"])

        assert body["updated"] == 0
        assert body["unchanged"] == 0

    @patch("group_cache_refresh.GroupFlattener")
    @patch("group_cache_refresh.IdentityStoreClient")
    def test_custom_attributes_preserved_on_group_change(
        self, MockClient, MockFlattener, dynamodb_table,
    ):
        """Custom attributes from existing cache are preserved when groups change."""
        table = dynamodb_table.Table(CACHE_TABLE)
        table.put_item(Item={
            "user_id": "u1",
            "upn": "alice@test.com",
            "groups": ["g1"],
            "source": "scim",
            "last_synced": "2026-01-01T00:00:00Z",
            "ttl_expiry": int(time.time()) + 86400,
            "custom_attributes": {"department": "Engineering", "ext_ClearanceLevel": "confidential"},
        })

        mock_client_inst = MagicMock()
        mock_client_inst.list_users.return_value = iter([
            {"UserId": "u1", "UserName": "alice@test.com"},
        ])
        MockClient.return_value = mock_client_inst

        mock_flattener_inst = MagicMock()
        mock_flattener_inst.flatten_all.return_value = {"u1": {"g1", "g2"}}
        MockFlattener.return_value = mock_flattener_inst

        from group_cache_refresh import handler
        result = handler({}, None)
        body = json.loads(result["body"])

        assert body["updated"] == 1

        item = table.get_item(Key={"user_id": "u1"}).get("Item")
        assert item["custom_attributes"]["department"] == "Engineering"
        assert item["custom_attributes"]["ext_ClearanceLevel"] == "confidential"
        assert set(item["groups"]) == {"g1", "g2"}
