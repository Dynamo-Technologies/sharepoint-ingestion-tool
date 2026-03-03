"""Quarantine manager — moves unmapped documents to a quarantine prefix in S3.

Documents that cannot be mapped to a permission prefix are moved from
``source/`` to ``quarantine/`` with metadata tags preserving the original
path, reason, and timestamp.  An SNS notification is published so that
operators can review quarantined items.
"""

import json
import logging
import os
from datetime import datetime, timezone
from urllib.parse import quote

import boto3

from config import config

logger = logging.getLogger(__name__)


class QuarantineManager:
    """Moves unmapped documents from ``source/`` to ``quarantine/`` in S3."""

    def __init__(
        self,
        bucket: str | None = None,
        sns_topic_arn: str | None = None,
        region: str | None = None,
    ):
        self.bucket = bucket or config.s3_bucket
        self._sns_topic_arn = sns_topic_arn or os.getenv("QUARANTINE_SNS_TOPIC_ARN", "")
        self._region = region or config.aws_region

        self._s3 = boto3.client("s3", region_name=self._region)
        self._sns = boto3.client("sns", region_name=self._region)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def quarantine_document(self, s3_key: str, reason: str = "no_mapping") -> str:
        """Move *s3_key* from source to quarantine prefix.

        - Copies the object with replacement tags (original path, reason, timestamp).
        - Deletes the source object.
        - Publishes an SNS notification (failures are logged, not raised).
        - Returns the new quarantine key.
        """
        quarantine_key = self._to_quarantine_key(s3_key)
        now = datetime.now(timezone.utc).isoformat()

        # Build tags string: Key1=Value1&Key2=Value2
        tags = {
            "original_prefix": s3_key,
            "quarantine_reason": reason,
            "quarantined_at": now,
        }
        tagging_str = self._encode_tags(tags)

        # Copy to quarantine with new tags
        self._s3.copy_object(
            Bucket=self.bucket,
            Key=quarantine_key,
            CopySource={"Bucket": self.bucket, "Key": s3_key},
            TaggingDirective="REPLACE",
            Tagging=tagging_str,
        )

        # Delete the source object
        self._s3.delete_object(Bucket=self.bucket, Key=s3_key)

        # Publish SNS notification
        self._publish_notification(s3_key, quarantine_key, reason, now)

        logger.warning(
            "Quarantined s3://%s/%s -> s3://%s/%s (reason=%s)",
            self.bucket, s3_key, self.bucket, quarantine_key, reason,
        )

        return quarantine_key

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_quarantine_key(s3_key: str) -> str:
        """Translate a source key to its quarantine equivalent.

        If the key starts with ``source/``, replace that prefix with
        ``quarantine/``.  Otherwise prepend ``quarantine/``.
        """
        if s3_key.startswith("source/"):
            return "quarantine/" + s3_key[len("source/"):]
        return "quarantine/" + s3_key

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _publish_notification(
        self, s3_key: str, quarantine_key: str, reason: str, timestamp: str,
    ) -> None:
        """Publish a quarantine event to SNS.  Failures are logged, not raised."""
        try:
            message = json.dumps({
                "s3_key": s3_key,
                "quarantine_key": quarantine_key,
                "reason": reason,
                "timestamp": timestamp,
                "bucket": self.bucket,
            })
            self._sns.publish(
                TopicArn=self._sns_topic_arn,
                Subject="Document quarantined",
                Message=message,
            )
        except Exception:
            logger.exception(
                "Failed to publish quarantine SNS notification for %s", s3_key,
            )

    @staticmethod
    def _encode_tags(tags: dict[str, str]) -> str:
        """Encode a tag dict to ``Key1=Value1&Key2=Value2`` format for S3."""
        parts = []
        for k, v in tags.items():
            parts.append(f"{quote(k, safe='')}={quote(v, safe='')}")
        return "&".join(parts)
