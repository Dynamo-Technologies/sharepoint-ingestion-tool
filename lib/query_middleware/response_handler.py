"""Format RAG query responses and privacy-safe denial messages.

The response handler produces structured response dicts for two cases:
1. Success — LLM response with citations from authorized chunks.
2. No results — a helpful message that does NOT reveal the existence
   of restricted documents.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Privacy-safe message when no chunks are returned.  Must never hint
# at the existence of restricted content.
_NO_RESULTS_MESSAGE = (
    "I don't have information on that topic in the documents available "
    "to you. You may want to check with the relevant department for "
    "additional resources."
)


class ResponseHandler:
    """Format query responses with citations or privacy-safe denials."""

    def format_success(
        self,
        llm_response_text: str,
        chunks: list[dict],
    ) -> dict:
        """Format a successful response with citations.

        Parameters
        ----------
        llm_response_text:
            The generated response from Bedrock InvokeModel.
        chunks:
            The retrieved chunks used as context (Bedrock Retrieve results).
        """
        citations = []
        for chunk in chunks:
            metadata = chunk.get("metadata") or {}
            content = chunk.get("content") or {}
            citations.append({
                "chunk_id": metadata.get("chunk_id", ""),
                "document_id": metadata.get("document_id", ""),
                "source_s3_key": metadata.get("source_s3_key", ""),
                "text_excerpt": content.get("text", "")[:200],
                "score": chunk.get("score", 0.0),
            })

        return {
            "response_text": llm_response_text,
            "citations": citations,
            "result_type": "success",
            "chunks_retrieved": len(chunks),
        }

    def format_no_results(self) -> dict:
        """Format a privacy-safe response when no chunks are returned."""
        return {
            "response_text": _NO_RESULTS_MESSAGE,
            "citations": [],
            "result_type": "no_results",
            "chunks_retrieved": 0,
        }
