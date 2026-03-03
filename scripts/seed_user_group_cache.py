#!/usr/bin/env python3
"""Seed the user-group-cache DynamoDB table from Entra ID exports.

Parses the Entra ID CSV exports and creates one DynamoDB item per user
with their flattened group memberships and custom attributes.

Usage:
    python scripts/seed_user_group_cache.py [--table TABLE_NAME] [--dry-run]
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import boto3

from lib.entra_id_parser import EntraIDParser


def build_user_cache_entries(export_dir: Path) -> list[dict]:
    """Parse Entra ID exports and build user-group cache entries."""
    parser = EntraIDParser(export_dir)
    data = parser.parse_all()

    now = datetime.now(timezone.utc)
    ttl_expiry = int(now.timestamp()) + (24 * 3600)  # 24 hours

    entries = []
    for user in data.users:
        if not user.is_active:
            continue

        # Get user's group memberships
        memberships = data.get_user_groups(user.id)
        group_ids = sorted(set(m.group_id for m in memberships))

        # Build custom attributes from extension attributes
        custom_attrs = {}
        for ext_key, ext_val in user.extension_attributes.items():
            # Map extensionAttribute1-15 to semantic names if known
            # For now, preserve the raw attribute names
            custom_attrs[ext_key] = ext_val

        # Add department and job title as attributes for sensitivity mapping
        if user.department:
            custom_attrs["department"] = user.department
        if user.job_title:
            custom_attrs["job_title"] = user.job_title

        entries.append({
            "user_id": user.id,
            "upn": user.user_principal_name,
            "groups": group_ids,
            "custom_attributes": custom_attrs,
            "last_synced": now.isoformat(),
            "source": "entra_id_export",
            "ttl_expiry": ttl_expiry,
        })

    return entries


def seed_table(table_name: str, entries: list[dict], dry_run: bool = False) -> int:
    """Write user cache entries to DynamoDB. Returns count of items written."""
    if dry_run:
        for e in entries[:5]:
            print(f"  [DRY RUN] {e['upn']} -> {len(e['groups'])} groups, "
                  f"{len(e['custom_attributes'])} attrs")
        if len(entries) > 5:
            print(f"  ... and {len(entries) - 5} more")
        return len(entries)

    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    table = dynamodb.Table(table_name)

    count = 0
    with table.batch_writer() as batch:
        for e in entries:
            batch.put_item(Item=e)
            count += 1

    return count


def main():
    parser = argparse.ArgumentParser(description="Seed user-group-cache table")
    parser.add_argument("--table", default="user-group-cache",
                        help="DynamoDB table name")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be written without writing")
    args = parser.parse_args()

    export_dir = ROOT / "entra-id"
    if not export_dir.exists():
        print(f"ERROR: {export_dir} not found.")
        return 1

    print(f"Parsing Entra ID exports from {export_dir}...")
    entries = build_user_cache_entries(export_dir)
    print(f"  Built {len(entries)} user cache entries")

    members = sum(1 for e in entries if not e["upn"].endswith(".onmicrosoft.com"))
    guests = len(entries) - members
    print(f"  Members: ~{members}, Guests: ~{guests}")

    avg_groups = sum(len(e["groups"]) for e in entries) / len(entries) if entries else 0
    print(f"  Average groups per user: {avg_groups:.1f}")

    print(f"\nSeeding {args.table} with {len(entries)} entries...")
    count = seed_table(args.table, entries, dry_run=args.dry_run)

    print(f"\nDone. {count} items {'would be ' if args.dry_run else ''}written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
