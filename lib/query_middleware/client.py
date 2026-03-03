"""Query middleware orchestrator — the single entry point for RAG queries.

Wires together group resolution, filter construction, Bedrock KB retrieval,
LLM generation, audit logging, and response formatting.  This middleware
is the ONLY path to the vector store for RAG queries.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import boto3

from lib.query_middleware.audit_logger import AuditLogger
from lib.query_middleware.filter_builder import FilterBuilder
from lib.query_middleware.group_resolver import GroupResolver
from lib.query_middleware.response_handler import ResponseHandler

logger = logging.getLogger(__name__)

# Default number of chunks to retrieve from Bedrock KB.
_DEFAULT_NUM_RESULTS = 10


class QueryMiddleware:
    """Permission-filtered RAG query orchestrator.

    Parameters
    ----------
    knowledge_base_id:
        The Bedrock Knowledge Base ID to query.
    model_id:
        The Bedrock model ID for generation (e.g.
        ``"anthropic.claude-3-sonnet-20240229-v1:0"``).
    group_resolver:
        Optional pre-configured ``GroupResolver``.
    bedrock_agent_client:
        Optional pre-configured ``boto3.client('bedrock-agent-runtime')``.
    bedrock_runtime_client:
        Optional pre-configured ``boto3.client('bedrock-runtime')``.
    num_results:
        Number of chunks to retrieve from the knowledge base.
    """

    def __init__(
        self,
        knowledge_base_id: str,
        model_id: str = "anthropic.claude-3-sonnet-20240229-v1:0",
        group_resolver: GroupResolver | None = None,
        bedrock_agent_client: Any | None = None,
        bedrock_runtime_client: Any | None = None,
        num_results: int = _DEFAULT_NUM_RESULTS,
    ) -> None:
        self._kb_id = knowledge_base_id
        self._model_id = model_id
        self._resolver = group_resolver or GroupResolver()
        self._bedrock_agent = bedrock_agent_client or boto3.client("bedrock-agent-runtime")
        self._bedrock_runtime = bedrock_runtime_client or boto3.client("bedrock-runtime")
        self._num_results = num_results
        self._filter_builder = FilterBuilder()
        self._response_handler = ResponseHandler()
        self._audit = AuditLogger()

    def query(
        self,
        query_text: str,
        user_id: str,
        user_groups: list[str] | None = None,
    ) -> dict:
        """Execute a permission-filtered RAG query.

        Parameters
        ----------
        query_text:
            The user's natural-language query.
        user_id:
            Entra ID User Object ID.
        user_groups:
            Group Object IDs from the SAML assertion.

        Returns
        -------
        dict
            Response with ``response_text``, ``citations``,
            ``result_type``, and ``chunks_retrieved``.
        """
        start_time = time.monotonic()
        resolved_groups: list[str] = []
        retrieval_filter: dict = {}
        upn = ""
        chunks: list[dict] = []
        result: dict = {}

        try:
            # 1. Resolve groups
            resolved = self._resolver.resolve(user_id, saml_groups=user_groups or [])
            upn = resolved.upn
            resolved_groups = resolved.groups

            # 2. Build filter
            retrieval_filter = self._filter_builder.build_filter(
                groups=resolved.groups,
                sensitivity_ceiling=resolved.sensitivity_ceiling,
            )

            # 3. Retrieve from Bedrock KB (filtered)
            retrieve_response = self._bedrock_agent.retrieve(
                knowledgeBaseId=self._kb_id,
                retrievalQuery={"text": query_text},
                retrievalConfiguration={
                    "vectorSearchConfiguration": {
                        "numberOfResults": self._num_results,
                        "filter": retrieval_filter,
                    },
                },
            )

            chunks = retrieve_response.get("retrievalResults", [])

            # 4. Check results
            if not chunks:
                result = self._response_handler.format_no_results()
            else:
                # 5. Generate response via Bedrock InvokeModel
                llm_text = self._invoke_model(query_text, chunks)
                result = self._response_handler.format_success(llm_text, chunks)

        except Exception:
            logger.exception("Query failed for user %s", user_id)
            result = self._response_handler.format_no_results()

        # 6. Audit log (always runs)
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        self._audit.log_query(
            user_id=user_id,
            user_upn=upn,
            resolved_groups=resolved_groups,
            filters_applied=retrieval_filter,
            chunk_ids=[
                c.get("metadata", {}).get("chunk_id", "") for c in chunks
            ],
            document_ids=list({
                c.get("metadata", {}).get("document_id", "") for c in chunks
            }),
            sensitivity_levels=list({
                c.get("metadata", {}).get("sensitivity_level", "") for c in chunks
            }),
            query_text=query_text,
            latency_ms=elapsed_ms,
            result_type=result["result_type"],
        )

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _invoke_model(self, query_text: str, chunks: list[dict]) -> str:
        """Call Bedrock InvokeModel with retrieved chunks as context."""
        context = "\n\n---\n\n".join(
            c.get("content", {}).get("text", "") for c in chunks
        )

        prompt = (
            "You are a helpful assistant answering questions based on company "
            "documents. Use ONLY the provided context to answer. If the context "
            "doesn't contain the answer, say so.\n\n"
            f"Context:\n{context}\n\n"
            f"Question: {query_text}\n\n"
            "Answer:"
        )

        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        })

        response = self._bedrock_runtime.invoke_model(
            modelId=self._model_id,
            body=body,
            contentType="application/json",
        )

        response_body = json.loads(response["body"].read())
        content_blocks = response_body.get("content", [])
        if not content_blocks:
            logger.warning("LLM returned empty content blocks")
            return ""
        return content_blocks[0].get("text", "")
