#!/usr/bin/env python3
"""Re-process quarantined documents after their prefix mapping has been added.

Lists quarantined documents, checks if a mapping now exists, and moves
them back to source/ to re-trigger the ingestion pipeline.

Usage:
    python scripts/reprocess_quarantined.py [--bucket BUCKET] [--dry-run]
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import boto3
from lib.dynamo_permissions.client import PermissionClient


def reprocess_quarantined(bucket: str, dry_run: bool = False) -> dict:
    """Move quarantined docs back to source/ if a mapping now exists."""
    s3 = boto3.client("s3", region_name="us-east-1")
    perm_client = PermissionClient()

    stats = {"reprocessed": 0, "still_unmapped": 0, "errors": 0, "total": 0}

    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="quarantine/"):
        for obj in page.get("Contents", []):
            quarantine_key = obj["Key"]
            stats["total"] += 1

            # Reconstruct original source key
            source_key = "source/" + quarantine_key[len("quarantine/"):]

            # Check if mapping now exists
            perm = perm_client.get_allowed_groups(source_key)
            if perm is None:
                stats["still_unmapped"] += 1
                if not dry_run:
                    print(f"  [STILL UNMAPPED] {quarantine_key}")
                continue

            if dry_run:
                print(f"  [DRY RUN] {quarantine_key} -> {source_key}")
                stats["reprocessed"] += 1
                continue

            try:
                # Copy back to source/
                s3.copy_object(
                    Bucket=bucket,
                    Key=source_key,
                    CopySource={"Bucket": bucket, "Key": quarantine_key},
                )
                # Delete from quarantine/
                s3.delete_object(Bucket=bucket, Key=quarantine_key)
                stats["reprocessed"] += 1
                print(f"  [REPROCESSED] {quarantine_key} -> {source_key}")

            except Exception as exc:
                print(f"  [ERROR] {quarantine_key}: {exc}")
                stats["errors"] += 1

    return stats


def main():
    parser = argparse.ArgumentParser(description="Re-process quarantined documents")
    parser.add_argument("--bucket", default="dynamo-ai-documents")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"Scanning quarantine/ in s3://{args.bucket}...")
    stats = reprocess_quarantined(args.bucket, args.dry_run)

    print(f"\nResults:")
    print(f"  Total quarantined: {stats['total']}")
    print(f"  Reprocessed:       {stats['reprocessed']}")
    print(f"  Still unmapped:    {stats['still_unmapped']}")
    print(f"  Errors:            {stats['errors']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
