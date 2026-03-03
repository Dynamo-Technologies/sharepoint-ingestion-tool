"""Tests for Entra ID CSV parser."""

import csv
import textwrap

import pytest

from lib.entra_id_parser.models import (
    ConditionalAccessPolicy,
    EntraData,
    EntraGroup,
    EntraUser,
    GroupMembership,
)
from lib.entra_id_parser.parser import EntraIDParser


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def export_dir(tmp_path):
    """Create a temporary export directory with sample CSV files."""
    return tmp_path


@pytest.fixture
def users_csv(export_dir):
    """Create a sample users CSV."""
    path = export_dir / "Dynamo_EntraID_Users_Export.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "DisplayName", "UserPrincipalName", "Id", "JobTitle",
            "Department", "OfficeLocation", "AccountEnabled", "UserType",
            "CreatedDateTime", "CompanyName", "City", "State", "Country",
        ])
        writer.writerow([
            "Alice Smith", "alice@dynamo.works", "user-001", "Consultant",
            "Engineering", "HQ", "True", "Member",
            "1/1/2024 12:00:00 PM", "Dynamo Technologies", "Vienna", "VA", "United States",
        ])
        writer.writerow([
            "Bob Guest", "bob_external.com#EXT#@dynamo.onmicrosoft.com", "user-002", "",
            "", "", "True", "Guest",
            "6/1/2024 12:00:00 PM", "External Corp", "", "", "",
        ])
        writer.writerow([
            "Carol Disabled", "carol@dynamo.works", "user-003", "Manager",
            "HR", "", "False", "Member",
            "1/1/2023 12:00:00 PM", "Dynamo Technologies", "", "", "",
        ])
    return path


@pytest.fixture
def groups_csv(export_dir):
    """Create a sample groups CSV."""
    path = export_dir / "Dynamo_EntraID_Groups_Export.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "DisplayName", "Id", "GroupTypes", "SecurityEnabled",
            "MailEnabled", "MembershipType", "MembershipRule",
            "Description", "CreatedDateTime",
        ])
        writer.writerow([
            "Engineering Team", "grp-001", "Unified", "False",
            "True", "Assigned", "",
            "Engineering group", "1/1/2024",
        ])
        writer.writerow([
            "SG - All Dynamo Users", "grp-002", "DynamicMembership", "True",
            "False", "Dynamic", "user.accountEnabled -eq true",
            "All active users", "1/1/2023",
        ])
        writer.writerow([
            "HR Team", "grp-003", "", "True",
            "False", "Assigned", "",
            "HR security group", "3/1/2024",
        ])
    return path


@pytest.fixture
def full_export_csv(export_dir):
    """Create a sample full export (group-member relationships)."""
    path = export_dir / "Dynamo_EntraID_Full_Export.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "GroupName", "GroupId", "GroupType", "MembershipType",
            "MemberName", "MemberUPN", "MemberJobTitle", "MemberDept",
            "MemberEnabled", "MemberId",
        ])
        writer.writerow([
            "Engineering Team", "grp-001", "Unified", "Assigned",
            "Alice Smith", "alice@dynamo.works", "Consultant", "Engineering",
            "True", "user-001",
        ])
        writer.writerow([
            "HR Team", "grp-003", "", "Assigned",
            "Carol Disabled", "carol@dynamo.works", "Manager", "HR",
            "False", "user-003",
        ])
        writer.writerow([
            "SG - All Dynamo Users", "grp-002", "DynamicMembership", "Dynamic",
            "Alice Smith", "alice@dynamo.works", "Consultant", "Engineering",
            "True", "user-001",
        ])
    return path


@pytest.fixture
def custom_attrs_csv(export_dir):
    """Create a sample custom attributes CSV."""
    path = export_dir / "Dynamo_CustomAttributes.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        headers = ["DisplayName", "UPN", "Id"] + [f"ExtAttr{i}" for i in range(1, 16)]
        writer.writerow(headers)
        row = ["Alice Smith", "alice@dynamo.works", "user-001", "ClearanceLevel3"] + [""] * 14
        writer.writerow(row)
        row2 = ["Bob Guest", "bob@external.com", "user-002"] + [""] * 15
        writer.writerow(row2)
    return path


@pytest.fixture
def conditional_access_csv(export_dir):
    """Create a sample conditional access CSV."""
    path = export_dir / "Dynamo_ConditionalAccess.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "DisplayName", "State", "IncludeUsers", "IncludeGroups",
            "ExcludeUsers", "ExcludeGroups", "IncludeApplications",
            "GrantControls", "CreatedDateTime", "ModifiedDateTime",
        ])
        writer.writerow([
            "Require MFA", "enabled", "All", "",
            "", "", "All",
            "mfa", "1/1/2024", "1/1/2025",
        ])
        writer.writerow([
            "Block Legacy", "disabled", "All", "",
            "", "", "All",
            "block", "6/1/2024", "6/1/2024",
        ])
    return path


@pytest.fixture
def error_log(export_dir):
    """Create a sample error log."""
    path = export_dir / "errors_on_User_Group_relationship_run.txt"
    path.write_text(textwrap.dedent("""\
        Get-MgUser : Resource 'abc-1230-def0' does not exist or one of its queried reference-property objects are not present.
        Status: 404 (NotFound)
        ErrorCode: Request_ResourceNotFound
        Get-MgUser : Resource 'def-4560-abc1' does not exist or one of its queried reference-property objects are not present.
        Status: 404 (NotFound)
        ErrorCode: Request_ResourceNotFound
    """))
    return path


@pytest.fixture
def full_export_dir(
    export_dir, users_csv, groups_csv, full_export_csv,
    custom_attrs_csv, conditional_access_csv, error_log,
):
    """Return a directory with all export files."""
    return export_dir


# ===================================================================
# Parser tests
# ===================================================================


class TestEntraIDParser:
    def test_init_invalid_dir(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            EntraIDParser(tmp_path / "nonexistent")

    def test_parse_all(self, full_export_dir):
        parser = EntraIDParser(full_export_dir)
        data = parser.parse_all()

        assert len(data.users) == 3
        assert len(data.groups) == 3
        assert len(data.memberships) == 3
        assert len(data.conditional_access_policies) == 2
        assert len(data.export_errors) == 2

    def test_parse_users(self, full_export_dir):
        parser = EntraIDParser(full_export_dir)
        data = parser.parse_all()

        alice = data.get_user_by_id("user-001")
        assert alice is not None
        assert alice.display_name == "Alice Smith"
        assert alice.department == "Engineering"
        assert alice.is_member
        assert alice.is_active
        assert alice.email_domain == "dynamo.works"

    def test_parse_guest_user(self, full_export_dir):
        parser = EntraIDParser(full_export_dir)
        data = parser.parse_all()

        bob = data.get_user_by_id("user-002")
        assert bob is not None
        assert bob.is_guest
        assert bob.is_active
        assert bob.email_domain == "external.com"

    def test_parse_disabled_user(self, full_export_dir):
        parser = EntraIDParser(full_export_dir)
        data = parser.parse_all()

        carol = data.get_user_by_id("user-003")
        assert carol is not None
        assert not carol.is_active
        assert carol.department == "HR"

    def test_parse_groups(self, full_export_dir):
        parser = EntraIDParser(full_export_dir)
        data = parser.parse_all()

        eng = data.get_group_by_name("Engineering Team")
        assert eng is not None
        assert eng.is_m365_group
        assert not eng.is_security_group
        assert not eng.is_dynamic

    def test_parse_dynamic_group(self, full_export_dir):
        parser = EntraIDParser(full_export_dir)
        data = parser.parse_all()

        sg = data.get_group_by_name("SG - All Dynamo Users")
        assert sg is not None
        assert sg.is_dynamic

    def test_parse_security_group(self, full_export_dir):
        parser = EntraIDParser(full_export_dir)
        data = parser.parse_all()

        hr = data.get_group_by_name("HR Team")
        assert hr is not None
        assert hr.is_security_group

    def test_parse_memberships(self, full_export_dir):
        parser = EntraIDParser(full_export_dir)
        data = parser.parse_all()

        eng_members = data.get_group_members("grp-001")
        assert len(eng_members) == 1
        assert eng_members[0].member_name == "Alice Smith"

    def test_user_groups_lookup(self, full_export_dir):
        parser = EntraIDParser(full_export_dir)
        data = parser.parse_all()

        alice_groups = data.get_user_groups("user-001")
        assert len(alice_groups) == 2
        group_names = {g.group_name for g in alice_groups}
        assert "Engineering Team" in group_names
        assert "SG - All Dynamo Users" in group_names

    def test_custom_attributes_merged(self, full_export_dir):
        parser = EntraIDParser(full_export_dir)
        data = parser.parse_all()

        alice = data.get_user_by_id("user-001")
        assert "extensionAttribute1" in alice.extension_attributes
        assert alice.extension_attributes["extensionAttribute1"] == "ClearanceLevel3"

    def test_conditional_access(self, full_export_dir):
        parser = EntraIDParser(full_export_dir)
        data = parser.parse_all()

        policies = data.conditional_access_policies
        enabled = [p for p in policies if p.is_enabled]
        assert len(enabled) == 1
        assert enabled[0].display_name == "Require MFA"

    def test_error_log_parsed(self, full_export_dir):
        parser = EntraIDParser(full_export_dir)
        data = parser.parse_all()

        assert len(data.export_errors) == 2
        assert data.export_errors[0].resource_id == "abc-1230-def0"
        assert data.export_errors[1].resource_id == "def-4560-abc1"

    def test_summary(self, full_export_dir):
        parser = EntraIDParser(full_export_dir)
        data = parser.parse_all()

        summary = data.summary()
        assert summary["total_users"] == 3
        assert summary["active_members"] == 1  # Alice (Carol is disabled)
        assert summary["active_guests"] == 1  # Bob
        assert summary["disabled_users"] == 1  # Carol
        assert summary["total_groups"] == 3
        assert summary["total_memberships"] == 3

    def test_upn_lookup_case_insensitive(self, full_export_dir):
        parser = EntraIDParser(full_export_dir)
        data = parser.parse_all()

        alice = data.get_user_by_upn("Alice@Dynamo.Works")
        assert alice is not None
        assert alice.display_name == "Alice Smith"


# ===================================================================
# Model tests
# ===================================================================


class TestEntraUser:
    def test_email_domain_member(self):
        u = EntraUser(
            display_name="Test", user_principal_name="test@dynamo.works", id="1"
        )
        assert u.email_domain == "dynamo.works"

    def test_email_domain_guest(self):
        u = EntraUser(
            display_name="Test",
            user_principal_name="test_external.com#EXT#@dynamo.onmicrosoft.com",
            id="1",
            user_type="Guest",
        )
        assert u.email_domain == "external.com"

    def test_email_domain_no_at(self):
        u = EntraUser(display_name="Test", user_principal_name="noemail", id="1")
        assert u.email_domain == ""


class TestEntraGroup:
    def test_m365_group(self):
        g = EntraGroup(display_name="Test", id="1", group_types="Unified")
        assert g.is_m365_group
        assert not g.is_dynamic

    def test_security_group_explicit(self):
        g = EntraGroup(display_name="Test", id="1", security_enabled=True)
        assert g.is_security_group

    def test_security_group_empty_type(self):
        g = EntraGroup(display_name="Test", id="1", group_types="")
        assert g.is_security_group


class TestEntraData:
    def test_empty_data(self):
        data = EntraData()
        data.build_indexes()
        assert data.get_user_by_id("nonexistent") is None
        assert data.get_group_by_id("nonexistent") is None
        assert data.get_group_members("nonexistent") == []
        assert data.get_user_groups("nonexistent") == []

    def test_active_members_filter(self):
        data = EntraData(
            users=[
                EntraUser("A", "a@x.com", "1", account_enabled=True, user_type="Member"),
                EntraUser("B", "b@x.com", "2", account_enabled=False, user_type="Member"),
                EntraUser("C", "c@x.com", "3", account_enabled=True, user_type="Guest"),
            ]
        )
        assert len(data.active_members) == 1
        assert data.active_members[0].display_name == "A"

    def test_m365_groups_filter(self):
        data = EntraData(
            groups=[
                EntraGroup("A", "1", group_types="Unified"),
                EntraGroup("B", "2", group_types=""),
                EntraGroup("C", "3", group_types="DynamicMembership"),
            ]
        )
        assert len(data.m365_groups) == 1
        assert data.m365_groups[0].display_name == "A"
