"""Tests for permission mapper and validator."""

import json

import pytest

from lib.entra_id_parser.models import (
    EntraData,
    EntraGroup,
    EntraUser,
    GroupMembership,
)
from lib.permission_mapper.mapper import (
    PermissionMapper,
    PermissionMappings,
    DEFAULT_GROUP_TAG_RULES,
)
from lib.permission_mapper.validator import MappingValidator, ValidationResult


# ===================================================================
# Fixtures
# ===================================================================


@pytest.fixture
def sample_entra_data():
    """Create sample Entra data with realistic group/user structure."""
    users = [
        EntraUser("Alice Eng", "alice@dynamo.works", "u1", "Consultant", "Engineering",
                   account_enabled=True, user_type="Member"),
        EntraUser("Bob HR", "bob@dynamo.works", "u2", "Manager", "HR",
                   account_enabled=True, user_type="Member"),
        EntraUser("Carol Finance", "carol@dynamo.works", "u3", "Controller", "Finance",
                   account_enabled=True, user_type="Member"),
        EntraUser("Dan BD", "dan@dynamo.works", "u4", "Director", "Business Development",
                   account_enabled=True, user_type="Member"),
        EntraUser("Eve Leadership", "eve@dynamo.works", "u5", "VP", "Leadership",
                   account_enabled=True, user_type="Member"),
        EntraUser("Frank Guest", "frank_ext.com#EXT#@dynamo.onmicrosoft.com", "u6", "",
                   "", account_enabled=True, user_type="Guest"),
        EntraUser("Grace Orphan", "grace@dynamo.works", "u7", "Analyst", "Operations",
                   account_enabled=True, user_type="Member"),
    ]

    groups = [
        EntraGroup("Engineering Team", "g1", group_types="Unified"),
        EntraGroup("HR Team", "g2", group_types="", security_enabled=True),
        EntraGroup("Finance", "g3", group_types="Unified"),
        EntraGroup("BD Pipeline Test", "g4", group_types="Unified"),
        EntraGroup("Leadership", "g5", group_types="Unified"),
        EntraGroup("Contracts", "g6", group_types="Unified"),
        EntraGroup("SG - All Dynamo Users", "g7", group_types="DynamicMembership"),
        EntraGroup("USDA FS ADAS -0176 Manager", "g8", group_types="", security_enabled=True),
        EntraGroup("Dynamo Technology Community of Interest (COI)", "g9", group_types="Unified"),
        EntraGroup("Recruiting", "g10", group_types="Unified"),
    ]

    memberships = [
        GroupMembership("Engineering Team", "g1", "Unified", "Assigned",
                        "Alice Eng", "alice@dynamo.works", "Consultant", "Engineering", True, "u1"),
        GroupMembership("HR Team", "g2", "", "Assigned",
                        "Bob HR", "bob@dynamo.works", "Manager", "HR", True, "u2"),
        GroupMembership("Finance", "g3", "Unified", "Assigned",
                        "Carol Finance", "carol@dynamo.works", "Controller", "Finance", True, "u3"),
        GroupMembership("BD Pipeline Test", "g4", "Unified", "Assigned",
                        "Dan BD", "dan@dynamo.works", "Director", "BD", True, "u4"),
        GroupMembership("Leadership", "g5", "Unified", "Assigned",
                        "Eve Leadership", "eve@dynamo.works", "VP", "Leadership", True, "u5"),
        GroupMembership("Contracts", "g6", "Unified", "Assigned",
                        "Eve Leadership", "eve@dynamo.works", "VP", "Leadership", True, "u5"),
        GroupMembership("SG - All Dynamo Users", "g7", "DynamicMembership", "Dynamic",
                        "Alice Eng", "alice@dynamo.works", "Consultant", "Engineering", True, "u1"),
        GroupMembership("Dynamo Technology Community of Interest (COI)", "g9", "Unified", "Assigned",
                        "Alice Eng", "alice@dynamo.works", "Consultant", "Engineering", True, "u1"),
        GroupMembership("Recruiting", "g10", "Unified", "Assigned",
                        "Bob HR", "bob@dynamo.works", "Manager", "HR", True, "u2"),
        # Guest in Finance group
        GroupMembership("Finance", "g3", "Unified", "Assigned",
                        "Frank Guest", "frank_ext.com#EXT#@dynamo.onmicrosoft.com", "", "", True, "u6"),
    ]

    data = EntraData(users=users, groups=groups, memberships=memberships)
    data.build_indexes()
    return data


@pytest.fixture
def mapper():
    return PermissionMapper()


@pytest.fixture
def known_tags():
    return {
        "hr", "finance", "bd", "capture", "engineering", "tech-leads",
        "contracts", "leadership", "admin", "all-staff",
    }


# ===================================================================
# PermissionMapper tests
# ===================================================================


class TestPermissionMapper:
    def test_generate_mappings(self, mapper, sample_entra_data):
        result = mapper.generate_mappings(sample_entra_data)
        assert isinstance(result, PermissionMappings)
        assert len(result.group_mappings) > 0
        assert len(result.user_permissions) > 0

    def test_engineering_group_mapped(self, mapper, sample_entra_data):
        result = mapper.generate_mappings(sample_entra_data)
        eng_mappings = [m for m in result.group_mappings if m.group_name == "Engineering Team"]
        assert len(eng_mappings) == 1
        assert "engineering" in eng_mappings[0].tags

    def test_hr_group_mapped(self, mapper, sample_entra_data):
        result = mapper.generate_mappings(sample_entra_data)
        hr_mappings = [m for m in result.group_mappings if m.group_name == "HR Team"]
        assert len(hr_mappings) == 1
        assert "hr" in hr_mappings[0].tags

    def test_finance_group_mapped(self, mapper, sample_entra_data):
        result = mapper.generate_mappings(sample_entra_data)
        fin_mappings = [m for m in result.group_mappings if m.group_name == "Finance"]
        assert len(fin_mappings) == 1
        assert "finance" in fin_mappings[0].tags

    def test_bd_group_mapped(self, mapper, sample_entra_data):
        result = mapper.generate_mappings(sample_entra_data)
        bd_mappings = [m for m in result.group_mappings if m.group_name == "BD Pipeline Test"]
        assert len(bd_mappings) == 1
        assert "bd" in bd_mappings[0].tags
        assert "capture" in bd_mappings[0].tags

    def test_leadership_group_mapped(self, mapper, sample_entra_data):
        result = mapper.generate_mappings(sample_entra_data)
        lead_mappings = [m for m in result.group_mappings if m.group_name == "Leadership"]
        assert len(lead_mappings) == 1
        assert "leadership" in lead_mappings[0].tags

    def test_contracts_group_mapped(self, mapper, sample_entra_data):
        result = mapper.generate_mappings(sample_entra_data)
        contracts = [m for m in result.group_mappings if m.group_name == "Contracts"]
        assert len(contracts) == 1
        assert "contracts" in contracts[0].tags

    def test_technology_coi_mapped(self, mapper, sample_entra_data):
        result = mapper.generate_mappings(sample_entra_data)
        tech_coi = [m for m in result.group_mappings
                    if "Technology" in m.group_name]
        assert len(tech_coi) == 1
        assert "engineering" in tech_coi[0].tags

    def test_recruiting_mapped_to_hr(self, mapper, sample_entra_data):
        result = mapper.generate_mappings(sample_entra_data)
        recruiting = [m for m in result.group_mappings if m.group_name == "Recruiting"]
        assert len(recruiting) == 1
        assert "hr" in recruiting[0].tags

    def test_unmapped_groups_identified(self, mapper, sample_entra_data):
        result = mapper.generate_mappings(sample_entra_data)
        unmapped_names = {g["group_name"] for g in result.unmapped_groups}
        # SG - All Dynamo Users and USDA manager group should be unmapped
        assert "SG - All Dynamo Users" in unmapped_names
        assert "USDA FS ADAS -0176 Manager" in unmapped_names

    def test_user_alice_has_engineering_tag(self, mapper, sample_entra_data):
        result = mapper.generate_mappings(sample_entra_data)
        alice = [u for u in result.user_permissions if u.user_id == "u1"]
        assert len(alice) == 1
        assert "engineering" in alice[0].tags
        assert "all-staff" in alice[0].tags

    def test_user_eve_has_multiple_tags(self, mapper, sample_entra_data):
        result = mapper.generate_mappings(sample_entra_data)
        eve = [u for u in result.user_permissions if u.user_id == "u5"]
        assert len(eve) == 1
        assert "leadership" in eve[0].tags
        assert "contracts" in eve[0].tags

    def test_orphan_user_not_in_permissions(self, mapper, sample_entra_data):
        result = mapper.generate_mappings(sample_entra_data)
        grace = [u for u in result.user_permissions if u.user_id == "u7"]
        assert len(grace) == 0  # Grace is in no mapped groups

    def test_stats_generated(self, mapper, sample_entra_data):
        result = mapper.generate_mappings(sample_entra_data)
        assert "total_groups" in result.stats
        assert "mapped_groups" in result.stats
        assert "users_with_tag_assignments" in result.stats

    def test_custom_rules(self, sample_entra_data):
        custom_rules = [
            {"pattern": "Engineering", "match_type": "contains", "tags": ["custom-eng"],
             "description": "Custom engineering rule"},
        ]
        mapper = PermissionMapper(rules=custom_rules)
        result = mapper.generate_mappings(sample_entra_data)

        eng = [m for m in result.group_mappings if m.group_name == "Engineering Team"]
        assert len(eng) == 1
        assert "custom-eng" in eng[0].tags

    def test_to_dict_serializable(self, mapper, sample_entra_data):
        result = mapper.generate_mappings(sample_entra_data)
        d = result.to_dict()
        # Should be JSON-serializable
        json_str = json.dumps(d)
        assert json_str

    def test_write_mappings(self, mapper, sample_entra_data, tmp_path):
        result = mapper.generate_mappings(sample_entra_data)
        output = tmp_path / "mappings.json"
        PermissionMapper.write_mappings(result, output)

        assert output.exists()
        with open(output) as f:
            data = json.load(f)
        assert data["version"] == "1.0"
        assert "generated_at" in data
        assert len(data["group_mappings"]) > 0


# ===================================================================
# Match type tests
# ===================================================================


class TestMatchTypes:
    def test_exact_match(self):
        mapper = PermissionMapper(rules=[
            {"pattern": "Finance", "match_type": "exact", "tags": ["fin"],
             "description": "Exact match"},
        ])
        data = EntraData(
            groups=[
                EntraGroup("Finance", "g1"),
                EntraGroup("Finance Team", "g2"),
            ]
        )
        data.build_indexes()
        result = mapper.generate_mappings(data)
        mapped_names = {m.group_name for m in result.group_mappings}
        assert "Finance" in mapped_names
        assert "Finance Team" not in mapped_names

    def test_prefix_match(self):
        mapper = PermissionMapper(rules=[
            {"pattern": "BD", "match_type": "prefix", "tags": ["bd"],
             "description": "Prefix match"},
        ])
        data = EntraData(
            groups=[
                EntraGroup("BD Events", "g1"),
                EntraGroup("BD Lessons Learned", "g2"),
                EntraGroup("Some BD Group", "g3"),
            ]
        )
        data.build_indexes()
        result = mapper.generate_mappings(data)
        mapped_names = {m.group_name for m in result.group_mappings}
        assert "BD Events" in mapped_names
        assert "BD Lessons Learned" in mapped_names
        assert "Some BD Group" not in mapped_names

    def test_suffix_match(self):
        mapper = PermissionMapper(rules=[
            {"pattern": "Managers", "match_type": "suffix", "tags": ["mgmt"],
             "description": "Suffix match"},
        ])
        data = EntraData(
            groups=[
                EntraGroup("Delivery Managers", "g1"),
                EntraGroup("Managers Club", "g2"),
            ]
        )
        data.build_indexes()
        result = mapper.generate_mappings(data)
        mapped_names = {m.group_name for m in result.group_mappings}
        assert "Delivery Managers" in mapped_names
        assert "Managers Club" not in mapped_names

    def test_contains_match(self):
        mapper = PermissionMapper(rules=[
            {"pattern": "Technology", "match_type": "contains", "tags": ["tech"],
             "description": "Contains match"},
        ])
        data = EntraData(
            groups=[
                EntraGroup("Dynamo Technology COI", "g1"),
                EntraGroup("Tech Team", "g2"),  # Doesn't contain "Technology"
            ]
        )
        data.build_indexes()
        result = mapper.generate_mappings(data)
        mapped_names = {m.group_name for m in result.group_mappings}
        assert "Dynamo Technology COI" in mapped_names
        assert "Tech Team" not in mapped_names

    def test_case_insensitive_matching(self):
        mapper = PermissionMapper(rules=[
            {"pattern": "HR", "match_type": "prefix", "tags": ["hr"],
             "description": "HR"},
        ])
        data = EntraData(
            groups=[
                EntraGroup("HR Team", "g1"),
                EntraGroup("hr case", "g2"),
                EntraGroup("HRCASE", "g3"),
            ]
        )
        data.build_indexes()
        result = mapper.generate_mappings(data)
        assert len(result.group_mappings) == 3


# ===================================================================
# Validator tests
# ===================================================================


class TestMappingValidator:
    def test_validate_returns_result(self, mapper, sample_entra_data, known_tags):
        mappings = mapper.generate_mappings(sample_entra_data)
        validator = MappingValidator(known_tags=known_tags)
        result = validator.validate(mappings, sample_entra_data)

        assert isinstance(result, ValidationResult)
        assert len(result.issues) > 0

    def test_coverage_stats(self, mapper, sample_entra_data, known_tags):
        mappings = mapper.generate_mappings(sample_entra_data)
        validator = MappingValidator(known_tags=known_tags)
        result = validator.validate(mappings, sample_entra_data)

        assert "total_groups" in result.coverage_stats
        assert "mapped_groups" in result.coverage_stats
        assert "user_coverage_pct" in result.coverage_stats

    def test_orphan_users_detected(self, mapper, sample_entra_data, known_tags):
        mappings = mapper.generate_mappings(sample_entra_data)
        validator = MappingValidator(known_tags=known_tags)
        result = validator.validate(mappings, sample_entra_data)

        orphan_ids = {u["user_id"] for u in result.orphan_users}
        # Grace (u7) is an active member not in any mapped group
        assert "u7" in orphan_ids

    def test_tag_distribution(self, mapper, sample_entra_data, known_tags):
        mappings = mapper.generate_mappings(sample_entra_data)
        validator = MappingValidator(known_tags=known_tags)
        result = validator.validate(mappings, sample_entra_data)

        assert "all-staff" in result.tag_distribution
        assert result.tag_distribution["all-staff"] > 0

    def test_unknown_tags_flagged(self, sample_entra_data):
        # Use a mapper with a tag not in access_rules.yaml
        custom_mapper = PermissionMapper(rules=[
            {"pattern": "Engineering", "match_type": "contains",
             "tags": ["unknown-tag"], "description": "test"},
        ])
        mappings = custom_mapper.generate_mappings(sample_entra_data)
        validator = MappingValidator(known_tags={"all-staff", "engineering"})
        result = validator.validate(mappings, sample_entra_data)

        error_issues = [i for i in result.issues
                        if i.severity == "error" and i.category == "consistency"]
        assert len(error_issues) == 1
        assert "unknown-tag" in error_issues[0].message

    def test_no_known_tags_skips_consistency(self, mapper, sample_entra_data):
        mappings = mapper.generate_mappings(sample_entra_data)
        validator = MappingValidator(known_tags=None)
        result = validator.validate(mappings, sample_entra_data)

        consistency_issues = [i for i in result.issues if i.category == "consistency"]
        skip_msg = [i for i in consistency_issues if "skipped" in i.message]
        assert len(skip_msg) == 1

    def test_guest_access_warning(self, mapper, sample_entra_data, known_tags):
        mappings = mapper.generate_mappings(sample_entra_data)
        validator = MappingValidator(known_tags=known_tags)
        result = validator.validate(mappings, sample_entra_data)

        # Frank Guest is in Finance group → should trigger a warning
        guest_issues = [i for i in result.issues
                        if "guest" in i.message.lower()]
        assert len(guest_issues) >= 1

    def test_is_valid_with_no_errors(self, mapper, sample_entra_data, known_tags):
        mappings = mapper.generate_mappings(sample_entra_data)
        validator = MappingValidator(known_tags=known_tags)
        result = validator.validate(mappings, sample_entra_data)
        # With the default rules + known_tags, there should be no errors
        assert result.is_valid

    def test_write_report(self, mapper, sample_entra_data, known_tags, tmp_path):
        mappings = mapper.generate_mappings(sample_entra_data)
        validator = MappingValidator(known_tags=known_tags)
        result = validator.validate(mappings, sample_entra_data)

        report_path = tmp_path / "report.md"
        MappingValidator.write_report(result, mappings, report_path)

        assert report_path.exists()
        content = report_path.read_text()
        assert "# Permission Mapping Validation Report" in content
        assert "Coverage Statistics" in content
        assert "Tag Distribution" in content
        assert "Mapped Groups" in content

    def test_report_contains_status(self, mapper, sample_entra_data, known_tags, tmp_path):
        mappings = mapper.generate_mappings(sample_entra_data)
        validator = MappingValidator(known_tags=known_tags)
        result = validator.validate(mappings, sample_entra_data)

        report_path = tmp_path / "report.md"
        MappingValidator.write_report(result, mappings, report_path)

        content = report_path.read_text()
        assert "**Status**: PASS" in content
