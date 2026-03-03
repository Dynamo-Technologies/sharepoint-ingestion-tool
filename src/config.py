"""Configuration loaded from environment variables and AWS Secrets Manager.

Environment variables take precedence.  When an Azure credential env var is
empty and ``SECRET_PREFIX`` is set (as in Lambda), the value is fetched from
AWS Secrets Manager instead.
"""

import logging
import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not needed in Lambda — env vars set by Terraform

logger = logging.getLogger(__name__)

_secrets_cache: dict[str, str] = {}


def _resolve_secret(env_var: str, secret_name: str) -> str:
    """Return *env_var* value if set, otherwise fetch from Secrets Manager.

    The secret is looked up as ``{SECRET_PREFIX}{secret_name}``.  Results are
    cached so each secret is fetched at most once per cold start.
    """
    value = os.getenv(env_var, "")
    if value:
        return value

    prefix = os.getenv("SECRET_PREFIX", "")
    if not prefix:
        return ""

    cache_key = f"{prefix}{secret_name}"
    if cache_key in _secrets_cache:
        return _secrets_cache[cache_key]

    try:
        import boto3

        region = os.getenv("AWS_REGION_NAME", os.getenv("AWS_REGION", "us-east-1"))
        client = boto3.client("secretsmanager", region_name=region)
        resp = client.get_secret_value(SecretId=cache_key)
        _secrets_cache[cache_key] = resp["SecretString"]
        return resp["SecretString"]
    except Exception:
        logger.warning(
            "Failed to fetch secret %s from Secrets Manager", cache_key, exc_info=True,
        )
        return ""


@dataclass(frozen=True)
class Config:
    # Azure / Microsoft Graph
    azure_client_id: str = _resolve_secret("AZURE_CLIENT_ID", "azure-client-id")
    azure_tenant_id: str = _resolve_secret("AZURE_TENANT_ID", "azure-tenant-id")
    azure_client_secret: str = _resolve_secret("AZURE_CLIENT_SECRET", "azure-client-secret")
    sharepoint_site_name: str = os.getenv("SHAREPOINT_SITE_NAME", "Dynamo")
    excluded_folders: list[str] = field(
        default_factory=lambda: os.getenv("EXCLUDED_FOLDERS", "Drafts,drafts").split(",")
    )

    # AWS S3
    s3_bucket: str = os.getenv("S3_BUCKET", "dynamo-ai-documents")
    s3_source_prefix: str = os.getenv("S3_SOURCE_PREFIX", "source")
    s3_extracted_prefix: str = os.getenv("S3_EXTRACTED_PREFIX", "extracted")
    aws_region: str = os.getenv("AWS_REGION", "us-east-1")

    # DynamoDB
    dynamodb_delta_table: str = os.getenv("DYNAMODB_DELTA_TABLE", "sp-ingest-delta-tokens")
    dynamodb_registry_table: str = os.getenv(
        "DYNAMODB_REGISTRY_TABLE", "sp-ingest-document-registry"
    )

    # Permission tables
    permission_mappings_table: str = os.getenv(
        "PERMISSION_MAPPINGS_TABLE", "doc-permission-mappings"
    )

    # Textract
    textract_sns_topic_arn: str = os.getenv("TEXTRACT_SNS_TOPIC_ARN", "")
    textract_sns_role_arn: str = os.getenv("TEXTRACT_SNS_ROLE_ARN", "")

    # Logging
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


config = Config()
