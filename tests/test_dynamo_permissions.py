"""Tests for DynamoDB permission client.

Uses moto to mock DynamoDB tables for isolated unit testing.
"""

import time
from datetime import datetime, timezone

import boto3
import moto
import pytest

from lib.dynamo_permissions.client import (
    AccessCheckResult,
    PermissionClient,
    PrefixPermission,
    UserGroupResult,
    SENSITIVITY_LEVELS,
)


# ===================================================================
# Fixtures
# ===================================================================

PERM_TABLE = "test-doc-permission-mappings"
CACHE_TABLE = "test-user-group-cache"


@pytest.fixture
def dynamodb_tables():
    """Create mocked DynamoDB tables for testing."""
    with moto.mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")

        # Create permission mappings table
        dynamodb.create_table(
            TableName=PERM_TABLE,
            KeySchema=[{"AttributeName": "s3_prefix", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "s3_prefix", "AttributeType": "S"},
                {"AttributeName": "sensitivity_level", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "sensitivity_level-index",
                    "KeySchema": [
                        {"AttributeName": "sensitivity_level", "KeyType": "HASH"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        # Create user-group cache table
        dynamodb.create_table(
            TableName=CACHE_TABLE,
            KeySchema=[{"AttributeName": "user_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "user_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        yield dynamodb


@pytest.fixture
def client(dynamodb_tables):
    """Create a PermissionClient backed by mocked tables."""
    return PermissionClient(
        permission_table_name=PERM_TABLE,
        user_cache_table_name=CACHE_TABLE,
        dynamodb_resource=dynamodb_tables,
    )


@pytest.fixture
def seeded_client(client):
    """Seed both tables with test data."""
    now = datetime.now(timezone.utc).isoformat()

    # Seed permission mappings with different prefix depths
    mappings = [
        PrefixPermission(
            s3_prefix="source/Dynamo",
            allowed_groups=["grp-all-1", "grp-all-2"],
            sensitivity_level="internal",
            last_updated=now,
            updated_by="test",
        ),
        PrefixPermission(
            s3_prefix="source/Dynamo/HR",
            allowed_groups=["grp-hr-1", "grp-hr-2"],
            sensitivity_level="confidential",
            last_updated=now,
            updated_by="test",
        ),
        PrefixPermission(
            s3_prefix="source/Dynamo/HR/Payroll",
            allowed_groups=["grp-hr-payroll"],
            sensitivity_level="restricted",
            last_updated=now,
            updated_by="test",
        ),
        PrefixPermission(
            s3_prefix="source/Dynamo/Finance",
            allowed_groups=["grp-finance-1"],
            sensitivity_level="confidential",
            last_updated=now,
            updated_by="test",
        ),
        PrefixPermission(
            s3_prefix="source/Dynamo/Engineering",
            allowed_groups=["grp-eng-1", "grp-eng-2"],
            sensitivity_level="internal",
            last_updated=now,
            updated_by="test",
        ),
    ]
    for m in mappings:
        client.put_permission_mapping(m)

    # Seed user-group cache
    future_ttl = int(time.time()) + 86400  # 24 hours from now
    past_ttl = int(time.time()) - 3600  # 1 hour ago (expired)

    # User with HR access
    client.put_user_cache(
        user_id="user-hr",
        upn="bob@dynamo.works",
        groups=["grp-hr-1", "grp-all-1"],
        custom_attributes={"department": "HR", "ext_ClearanceLevel": "confidential"},
        source="entra_id_export",
    )

    # User with HR Payroll access
    client.put_user_cache(
        user_id="user-hr-payroll",
        upn="charlie@dynamo.works",
        groups=["grp-hr-payroll", "grp-hr-1", "grp-all-1"],
        custom_attributes={"ext_ClearanceLevel": "secret"},
        source="entra_id_export",
    )

    # User with engineering access
    client.put_user_cache(
        user_id="user-eng",
        upn="alice@dynamo.works",
        groups=["grp-eng-1", "grp-all-1"],
        custom_attributes={"department": "Engineering"},
        source="entra_id_export",
    )

    # User with no matching groups
    client.put_user_cache(
        user_id="user-intern",
        upn="intern@dynamo.works",
        groups=["grp-unrelated"],
        source="entra_id_export",
    )

    # User with expired cache (manually set expired TTL)
    cache_table = client._cache_table
    cache_table.put_item(Item={
        "user_id": "user-expired",
        "upn": "expired@dynamo.works",
        "groups": ["grp-hr-1"],
        "custom_attributes": {},
        "last_synced": now,
        "source": "entra_id_export",
        "ttl_expiry": past_ttl,
    })

    # User with direct sensitivity override
    client.put_user_cache(
        user_id="user-restricted",
        upn="restricted@dynamo.works",
        groups=["grp-all-1"],
        custom_attributes={
            "ext_DataSensitivity": "restricted",
            "ext_ClearanceLevel": "internal",
        },
        source="entra_id_export",
    )

    # User with no custom attributes
    client.put_user_cache(
        user_id="user-basic",
        upn="basic@dynamo.works",
        groups=["grp-all-1"],
        source="entra_id_export",
    )

    # User with public clearance
    client.put_user_cache(
        user_id="user-public",
        upn="public@dynamo.works",
        groups=["grp-all-1"],
        custom_attributes={"ext_ClearanceLevel": "public"},
        source="entra_id_export",
    )

    return client


# ===================================================================
# get_allowed_groups tests
# ===================================================================


class TestGetAllowedGroups:
    def test_exact_prefix_match(self, seeded_client):
        result = seeded_client.get_allowed_groups("source/Dynamo/HR")
        assert result is not None
        assert result.s3_prefix == "source/Dynamo/HR"
        assert "grp-hr-1" in result.allowed_groups
        assert "grp-hr-2" in result.allowed_groups

    def test_longest_prefix_match(self, seeded_client):
        """A key under HR/Payroll should match the Payroll prefix, not HR."""
        result = seeded_client.get_allowed_groups("source/Dynamo/HR/Payroll/salaries.pdf")
        assert result is not None
        assert result.s3_prefix == "source/Dynamo/HR/Payroll"
        assert "grp-hr-payroll" in result.allowed_groups

    def test_parent_prefix_match(self, seeded_client):
        """A key under HR/Policies should match HR prefix (no Policies-specific prefix)."""
        result = seeded_client.get_allowed_groups("source/Dynamo/HR/Policies/handbook.pdf")
        assert result is not None
        assert result.s3_prefix == "source/Dynamo/HR"

    def test_root_prefix_match(self, seeded_client):
        """Unknown library falls back to the site-level prefix."""
        result = seeded_client.get_allowed_groups("source/Dynamo/Random-Library/doc.pdf")
        assert result is not None
        assert result.s3_prefix == "source/Dynamo"
        assert "grp-all-1" in result.allowed_groups

    def test_no_match_returns_none(self, seeded_client):
        """Completely unmatched prefix returns None (quarantine)."""
        result = seeded_client.get_allowed_groups("other-bucket/documents/file.pdf")
        assert result is None

    def test_sensitivity_level_returned(self, seeded_client):
        result = seeded_client.get_allowed_groups("source/Dynamo/HR")
        assert result.sensitivity_level == "confidential"

    def test_engineering_prefix(self, seeded_client):
        result = seeded_client.get_allowed_groups("source/Dynamo/Engineering/docs/readme.md")
        assert result is not None
        assert result.s3_prefix == "source/Dynamo/Engineering"
        assert "grp-eng-1" in result.allowed_groups

    def test_normalized_slashes(self, seeded_client):
        """Leading/trailing slashes should be stripped for matching."""
        result = seeded_client.get_allowed_groups("/source/Dynamo/HR/")
        assert result is not None
        assert result.s3_prefix == "source/Dynamo/HR"

    def test_empty_table_returns_none(self, client):
        """Empty permission table returns None."""
        result = client.get_allowed_groups("source/Dynamo/HR/doc.pdf")
        assert result is None


# ===================================================================
# get_user_groups tests
# ===================================================================


class TestGetUserGroups:
    def test_existing_user(self, seeded_client):
        result = seeded_client.get_user_groups("user-hr")
        assert result.cache_hit is True
        assert result.cache_expired is False
        assert "grp-hr-1" in result.groups
        assert result.upn == "bob@dynamo.works"

    def test_nonexistent_user(self, seeded_client):
        result = seeded_client.get_user_groups("user-doesnt-exist")
        assert result.cache_hit is False
        assert result.groups == []

    def test_expired_cache_entry(self, seeded_client):
        result = seeded_client.get_user_groups("user-expired")
        assert result.cache_hit is True
        assert result.cache_expired is True
        assert "grp-hr-1" in result.groups  # Data still returned

    def test_custom_attributes_returned(self, seeded_client):
        result = seeded_client.get_user_groups("user-hr")
        assert result.custom_attributes.get("department") == "HR"
        assert result.custom_attributes.get("ext_ClearanceLevel") == "confidential"

    def test_source_field(self, seeded_client):
        result = seeded_client.get_user_groups("user-eng")
        assert result.source == "entra_id_export"


# ===================================================================
# check_access tests
# ===================================================================


class TestCheckAccess:
    def test_authorized_user(self, seeded_client):
        """User with HR group can access HR documents."""
        result = seeded_client.check_access("user-hr", "source/Dynamo/HR/doc.pdf")
        assert result.allowed is True
        assert result.reason == "authorized"
        assert "grp-hr-1" in result.matching_groups

    def test_unauthorized_user(self, seeded_client):
        """User with engineering groups cannot access HR documents."""
        result = seeded_client.check_access("user-eng", "source/Dynamo/HR/doc.pdf")
        assert result.allowed is False
        assert result.reason == "no_group_match"

    def test_user_with_expired_cache(self, seeded_client):
        """User with expired cache still gets checked but flagged."""
        result = seeded_client.check_access("user-expired", "source/Dynamo/HR/doc.pdf")
        assert result.allowed is True  # grp-hr-1 matches
        assert result.cache_expired is True

    def test_nonexistent_user(self, seeded_client):
        """Unknown user is denied access."""
        result = seeded_client.check_access("user-ghost", "source/Dynamo/HR/doc.pdf")
        assert result.allowed is False
        assert result.reason == "user_not_found"

    def test_no_mapping_for_prefix(self, seeded_client):
        """Unmapped prefix is denied."""
        result = seeded_client.check_access("user-hr", "other-bucket/doc.pdf")
        assert result.allowed is False
        assert result.reason == "no_mapping"

    def test_user_accesses_via_parent_prefix(self, seeded_client):
        """User with grp-all-1 can access docs via root prefix."""
        result = seeded_client.check_access(
            "user-intern", "source/Dynamo/Random-Library/doc.pdf"
        )
        # user-intern only has grp-unrelated, root has grp-all-1, grp-all-2
        assert result.allowed is False

    def test_authorized_via_root_prefix(self, seeded_client):
        """User with grp-all-1 can access docs matched by root prefix."""
        result = seeded_client.check_access(
            "user-eng", "source/Dynamo/Random-Library/doc.pdf"
        )
        # user-eng has grp-all-1 which is in root prefix
        assert result.allowed is True
        assert result.matched_prefix == "source/Dynamo"

    def test_payroll_access_hierarchy(self, seeded_client):
        """Only payroll user can access payroll docs."""
        # HR user without payroll group
        result = seeded_client.check_access(
            "user-hr", "source/Dynamo/HR/Payroll/salaries.xlsx"
        )
        assert result.allowed is False  # grp-hr-1 not in payroll prefix

        # Payroll user
        result = seeded_client.check_access(
            "user-hr-payroll", "source/Dynamo/HR/Payroll/salaries.xlsx"
        )
        assert result.allowed is True

    def test_matched_prefix_returned(self, seeded_client):
        result = seeded_client.check_access("user-hr", "source/Dynamo/HR/policies/pto.pdf")
        assert result.matched_prefix == "source/Dynamo/HR"


# ===================================================================
# get_user_sensitivity_ceiling tests
# ===================================================================


class TestGetUserSensitivityCeiling:
    def test_confidential_clearance(self, seeded_client):
        """User with ext_ClearanceLevel=confidential gets confidential ceiling."""
        result = seeded_client.get_user_sensitivity_ceiling("user-hr")
        assert result == "confidential"

    def test_secret_clearance(self, seeded_client):
        """User with ext_ClearanceLevel=secret gets restricted ceiling."""
        result = seeded_client.get_user_sensitivity_ceiling("user-hr-payroll")
        assert result == "restricted"

    def test_direct_sensitivity_override(self, seeded_client):
        """ext_DataSensitivity takes precedence over ext_ClearanceLevel."""
        result = seeded_client.get_user_sensitivity_ceiling("user-restricted")
        assert result == "restricted"

    def test_no_attributes_returns_internal(self, seeded_client):
        """User with no custom attributes defaults to internal."""
        result = seeded_client.get_user_sensitivity_ceiling("user-basic")
        assert result == "internal"

    def test_public_clearance(self, seeded_client):
        """User with ext_ClearanceLevel=public gets public ceiling."""
        result = seeded_client.get_user_sensitivity_ceiling("user-public")
        assert result == "public"

    def test_nonexistent_user_returns_public(self, seeded_client):
        """Unknown user gets public (most restrictive)."""
        result = seeded_client.get_user_sensitivity_ceiling("user-ghost")
        assert result == "public"

    def test_engineering_user_defaults_internal(self, seeded_client):
        """User with department but no clearance attributes defaults to internal."""
        result = seeded_client.get_user_sensitivity_ceiling("user-eng")
        assert result == "internal"


# ===================================================================
# Admin helpers tests
# ===================================================================


class TestAdminHelpers:
    def test_put_and_get_permission_mapping(self, client):
        now = datetime.now(timezone.utc).isoformat()
        client.put_permission_mapping(PrefixPermission(
            s3_prefix="source/Test",
            allowed_groups=["grp-1", "grp-2"],
            sensitivity_level="internal",
            custom_filters={"project_code": "P001"},
            last_updated=now,
            updated_by="test",
        ))
        result = client.get_allowed_groups("source/Test/doc.pdf")
        assert result is not None
        assert result.allowed_groups == ["grp-1", "grp-2"]
        assert result.sensitivity_level == "internal"

    def test_put_and_get_user_cache(self, client):
        client.put_user_cache(
            user_id="u-test",
            upn="test@dynamo.works",
            groups=["g1", "g2"],
            custom_attributes={"dept": "IT"},
            source="manual",
            ttl_hours=48,
        )
        result = client.get_user_groups("u-test")
        assert result.cache_hit is True
        assert result.groups == ["g1", "g2"]
        assert result.upn == "test@dynamo.works"
        assert result.custom_attributes["dept"] == "IT"
        assert result.source == "manual"
        assert result.cache_expired is False


# ===================================================================
# Sensitivity levels
# ===================================================================


class TestSensitivityLevels:
    def test_levels_ordered(self):
        assert SENSITIVITY_LEVELS == ["public", "internal", "confidential", "restricted"]

    def test_all_levels_valid_for_ceiling(self):
        """All defined sensitivity levels should be valid return values."""
        for level in SENSITIVITY_LEVELS:
            assert level in SENSITIVITY_LEVELS
