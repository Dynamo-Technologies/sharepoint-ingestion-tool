#!/usr/bin/env python3
"""Retag existing S3 source documents with DynamoDB permission metadata.

Scans all objects under source/ prefix, looks up permission mappings, and
applies allowed_groups + sensitivity_level as S3 object tags.

Usage:
    python scripts/retag_existing_documents.py [--bucket BUCKET] [--dry-run] [--limit N]
"""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import boto3
from lib.dynamo_permissions.client import PermissionClient


def retag_documents(
    bucket: str,
    prefix: str = "source/",
    dry_run: bool = False,
    limit: int = 0,
) -> dict:
    """Scan S3 and apply permission tags to each document.

    Returns stats dict with tagged, quarantine_candidates, errors, skipped.
    """
    s3 = boto3.client("s3", region_name="us-east-1")
    perm_client = PermissionClient()

    stats = {"tagged": 0, "quarantine_candidates": 0, "errors": 0, "skipped": 0, "total": 0}
    quarantine_list = []

    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            s3_key = obj["Key"]
            stats["total"] += 1

            if limit and stats["total"] > limit:
                return stats

            # Look up permission mapping
            perm = perm_client.get_allowed_groups(s3_key)

            if perm is None:
                stats["quarantine_candidates"] += 1
                quarantine_list.append(s3_key)
                if not dry_run:
                    print(f"  [QUARANTINE] {s3_key}")
                continue

            new_tags = {
                "allowed_groups": ",".join(perm.allowed_groups),
                "sensitivity_level": perm.sensitivity_level,
                "matched_prefix": perm.s3_prefix,
            }

            if dry_run:
                print(f"  [DRY RUN] {s3_key} -> {perm.sensitivity_level} ({len(perm.allowed_groups)} groups)")
                stats["tagged"] += 1
                continue

            try:
                # Read existing tags and merge
                existing = s3.get_object_tagging(Bucket=bucket, Key=s3_key)
                tag_set = {t["Key"]: t["Value"] for t in existing.get("TagSet", [])}
                tag_set.update(new_tags)

                s3.put_object_tagging(
                    Bucket=bucket,
                    Key=s3_key,
                    Tagging={
                        "TagSet": [{"Key": k, "Value": v} for k, v in tag_set.items()],
                    },
                )
                stats["tagged"] += 1

            except Exception as exc:
                print(f"  [ERROR] {s3_key}: {exc}")
                stats["errors"] += 1

    if quarantine_list:
        print(f"\nQuarantine candidates ({len(quarantine_list)}):")
        for key in quarantine_list[:20]:
            print(f"  {key}")
        if len(quarantine_list) > 20:
            print(f"  ... and {len(quarantine_list) - 20} more")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Retag existing S3 documents with permission metadata")
    parser.add_argument("--bucket", default="dynamo-ai-documents")
    parser.add_argument("--prefix", default="source/")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Process at most N documents")
    args = parser.parse_args()

    print(f"Scanning s3://{args.bucket}/{args.prefix}...")
    stats = retag_documents(args.bucket, args.prefix, args.dry_run, args.limit)

    print(f"\nResults:")
    print(f"  Total scanned: {stats['total']}")
    print(f"  Tagged:         {stats['tagged']}")
    print(f"  Quarantine:     {stats['quarantine_candidates']}")
    print(f"  Errors:         {stats['errors']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
