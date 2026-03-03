"""Structured JSON audit logging for RAG queries.

Every query is logged with user identity, resolved permissions, filter
configuration, retrieved chunks, and timing — but never the raw query
text (only a SHA-256 hash for privacy).

Logs to the ``query_middleware.audit`` Python logger at INFO level.
In Lambda, this writes to CloudWatch Logs as structured JSON.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("query_middleware.audit")


class AuditLogger:
    """Emit structured JSON audit entries for every RAG query."""

    def log_query(
        self,
        *,
        user_id: str,
        user_upn: str,
        resolved_groups: list[str],
        filters_applied: dict,
        chunk_ids: list[str],
        document_ids: list[str],
        sensitivity_levels: list[str],
        query_text: str,
        latency_ms: int,
        result_type: str,
    ) -> None:
        """Log a single query audit entry.

        Parameters
        ----------
        query_text:
            The original query — hashed before logging; never stored
            in plaintext.
        result_type:
            One of ``"success"``, ``"no_results"``.
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_id": user_id,
            "user_upn": user_upn,
            "resolved_groups": resolved_groups,
            "filters_applied": filters_applied,
            "chunk_ids_retrieved": chunk_ids,
            "source_document_ids": document_ids,
            "sensitivity_levels": sensitivity_levels,
            "query_text_hash": hashlib.sha256(query_text.encode()).hexdigest(),
            "response_latency_ms": latency_ms,
            "result_type": result_type,
        }

        logger.info(json.dumps(entry, default=str))
