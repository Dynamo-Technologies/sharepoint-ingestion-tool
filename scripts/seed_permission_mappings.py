#!/usr/bin/env python3
"""Seed the doc-permission-mappings DynamoDB table from permission_mappings.json.

Reads config/permission_mappings.json and creates one DynamoDB item per
unique access-tag combination, mapping S3 prefixes to allowed Entra group IDs.

Usage:
    python scripts/seed_permission_mappings.py [--table TABLE_NAME] [--dry-run]
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import boto3


def build_prefix_mappings(mappings_path: Path) -> list[dict]:
    """Build S3 prefix → group mappings from permission_mappings.json.

    Groups the Entra group IDs by the access tags they map to, then
    creates one DynamoDB item per S3 library prefix pattern.
    """
    with open(mappings_path) as f:
        data = json.load(f)

    # Map: access_tag → list of group IDs
    tag_to_groups: dict[str, list[str]] = defaultdict(list)
    for gm in data["group_mappings"]:
        for tag in gm["access_tags"]:
            tag_to_groups[tag].append(gm["group_id"])

    # Deduplicate groups per tag
    for tag in tag_to_groups:
        tag_to_groups[tag] = sorted(set(tag_to_groups[tag]))

    # Build prefix mappings matching the access_rules.yaml patterns
    # These correspond to the S3 prefix structure: source/{site}/{library}/
    now = datetime.now(timezone.utc).isoformat()
    prefix_mappings = [
        {
            "s3_prefix": "source/Dynamo/HR",
            "allowed_groups": tag_to_groups.get("hr", []),
            "sensitivity_level": "confidential",
            "custom_filters": {},
            "last_updated": now,
            "updated_by": "seed_script",
        },
        {
            "s3_prefix": "source/Dynamo/Finance",
            "allowed_groups": tag_to_groups.get("finance", []),
            "sensitivity_level": "confidential",
            "custom_filters": {},
            "last_updated": now,
            "updated_by": "seed_script",
        },
        {
            "s3_prefix": "source/Dynamo/BD",
            "allowed_groups": (
                tag_to_groups.get("bd", []) + tag_to_groups.get("capture", [])
            ),
            "sensitivity_level": "internal",
            "custom_filters": {},
            "last_updated": now,
            "updated_by": "seed_script",
        },
        {
            "s3_prefix": "source/Dynamo/Engineering",
            "allowed_groups": tag_to_groups.get("engineering", []),
            "sensitivity_level": "internal",
            "custom_filters": {},
            "last_updated": now,
            "updated_by": "seed_script",
        },
        {
            "s3_prefix": "source/Dynamo/Contracts",
            "allowed_groups": tag_to_groups.get("contracts", []),
            "sensitivity_level": "confidential",
            "custom_filters": {},
            "last_updated": now,
            "updated_by": "seed_script",
        },
        {
            "s3_prefix": "source/Dynamo",
            "allowed_groups": sorted(
                set(
                    gid
                    for groups in tag_to_groups.values()
                    for gid in groups
                )
            ),
            "sensitivity_level": "internal",
            "custom_filters": {},
            "last_updated": now,
            "updated_by": "seed_script",
        },
    ]

    # Deduplicate allowed_groups
    for pm in prefix_mappings:
        pm["allowed_groups"] = sorted(set(pm["allowed_groups"]))

    return prefix_mappings


def seed_table(table_name: str, mappings: list[dict], dry_run: bool = False) -> int:
    """Write prefix mappings to DynamoDB. Returns count of items written."""
    if dry_run:
        for m in mappings:
            print(f"  [DRY RUN] {m['s3_prefix']} -> {len(m['allowed_groups'])} groups "
                  f"(sensitivity={m['sensitivity_level']})")
        return len(mappings)

    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    table = dynamodb.Table(table_name)

    count = 0
    with table.batch_writer() as batch:
        for m in mappings:
            batch.put_item(Item=m)
            count += 1
            print(f"  {m['s3_prefix']} -> {len(m['allowed_groups'])} groups "
                  f"(sensitivity={m['sensitivity_level']})")

    return count


def main():
    parser = argparse.ArgumentParser(description="Seed doc-permission-mappings table")
    parser.add_argument("--table", default="doc-permission-mappings",
                        help="DynamoDB table name")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be written without writing")
    args = parser.parse_args()

    mappings_path = ROOT / "config" / "permission_mappings.json"
    if not mappings_path.exists():
        print(f"ERROR: {mappings_path} not found. Run generate_permission_mappings.py first.")
        return 1

    print(f"Building prefix mappings from {mappings_path}...")
    prefix_mappings = build_prefix_mappings(mappings_path)

    print(f"\nSeeding {args.table} with {len(prefix_mappings)} entries...")
    count = seed_table(args.table, prefix_mappings, dry_run=args.dry_run)

    print(f"\nDone. {count} items {'would be ' if args.dry_run else ''}written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
