#!/usr/bin/env python3
"""Generate permission_mappings.json and validation report from Entra ID exports.

Usage:
    python scripts/generate_permission_mappings.py

Reads CSV exports from entra-id/ and produces:
- config/permission_mappings.json
- docs/PERMISSION_MAPPING_VALIDATION.md
"""

import sys
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from lib.entra_id_parser import EntraIDParser
from lib.permission_mapper.mapper import PermissionMapper
from lib.permission_mapper.validator import MappingValidator

# Known tags from src/config/access_rules.yaml
KNOWN_TAGS = {
    "hr", "finance", "bd", "capture", "engineering", "tech-leads",
    "contracts", "leadership", "admin", "all-staff",
}


def main():
    export_dir = ROOT / "entra-id"
    print(f"Parsing Entra ID exports from {export_dir}...")

    parser = EntraIDParser(export_dir)
    data = parser.parse_all()

    summary = data.summary()
    print(f"  Users: {summary['total_users']} ({summary['active_members']} active members, "
          f"{summary['active_guests']} active guests, {summary['disabled_users']} disabled)")
    print(f"  Groups: {summary['total_groups']} ({summary['m365_groups']} M365, "
          f"{summary['security_groups']} security, {summary['dynamic_groups']} dynamic)")
    print(f"  Memberships: {summary['total_memberships']}")
    print(f"  Conditional access policies: {summary['conditional_access_policies']}")
    print(f"  Export errors: {summary['export_errors']}")

    print("\nGenerating permission mappings...")
    mapper = PermissionMapper()
    mappings = mapper.generate_mappings(data)

    output_json = ROOT / "config" / "permission_mappings.json"
    PermissionMapper.write_mappings(mappings, output_json)
    print(f"  Wrote {output_json}")
    print(f"  Mapped groups: {mappings.stats['mapped_groups']}")
    print(f"  Users with tags: {mappings.stats['users_with_tag_assignments']}")

    print("\nValidating mappings...")
    validator = MappingValidator(known_tags=KNOWN_TAGS)
    result = validator.validate(mappings, data)

    report_path = ROOT / "docs" / "PERMISSION_MAPPING_VALIDATION.md"
    MappingValidator.write_report(result, mappings, report_path)
    print(f"  Wrote {report_path}")
    print(f"  Status: {'PASS' if result.is_valid else 'FAIL'}")
    print(f"  Errors: {result.error_count}, Warnings: {result.warning_count}, Info: {result.info_count}")

    return 0 if result.is_valid else 1


if __name__ == "__main__":
    sys.exit(main())
