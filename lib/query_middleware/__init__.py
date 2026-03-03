"""Permission-filtered query middleware for the RAG pipeline.

Intercepts every query, resolves user permissions from SAML assertions
and DynamoDB cache, applies mandatory filters to Bedrock KB retrieval,
and ensures the LLM never sees unauthorized content.
"""

from lib.query_middleware.client import QueryMiddleware
from lib.query_middleware.group_resolver import GroupResolver, ResolvedUser
from lib.query_middleware.filter_builder import FilterBuilder
from lib.query_middleware.audit_logger import AuditLogger
from lib.query_middleware.response_handler import ResponseHandler
from lib.query_middleware.metadata_exporter import MetadataExporter

__all__ = [
    "QueryMiddleware",
    "GroupResolver",
    "ResolvedUser",
    "FilterBuilder",
    "AuditLogger",
    "ResponseHandler",
    "MetadataExporter",
]
