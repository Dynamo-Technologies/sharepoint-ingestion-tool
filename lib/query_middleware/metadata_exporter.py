"""Export chunk metadata to Bedrock Knowledge Base sidecar format.

Bedrock KB requires ``.metadata.json`` files alongside content files.
This module takes chunk dicts (from ``DocumentChunker``) and produces
the sidecar format with ``metadataAttributes`` including the numeric
sensitivity level needed for filter comparison.
"""

from __future__ import annotations

import logging

from lib.query_middleware.filter_builder import SENSITIVITY_MAP

logger = logging.getLogger(__name__)


class MetadataExporter:
    """Convert chunker output to Bedrock KB metadata sidecar format."""

    def export_chunk_metadata(self, chunk: dict) -> dict:
        """Produce a Bedrock KB ``.metadata.json`` dict for a single chunk.

        Parameters
        ----------
        chunk:
            A chunk dict from ``DocumentChunker.chunk_document()``.
            Expected top-level keys: ``allowed_groups``,
            ``sensitivity_level``, ``s3_prefix``, ``document_id``,
            ``chunk_id``, ``source_s3_key``, ``metadata``.
        """
        sensitivity = chunk.get("sensitivity_level", "")
        inner_meta = chunk.get("metadata", {})

        return {
            "metadataAttributes": {
                "allowed_groups": chunk.get("allowed_groups", []),
                "sensitivity_level": sensitivity,
                "sensitivity_level_numeric": SENSITIVITY_MAP.get(
                    sensitivity.lower(), 0
                ) if sensitivity else 0,
                "document_id": chunk.get("document_id", ""),
                "chunk_id": chunk.get("chunk_id", ""),
                "source_s3_key": chunk.get("source_s3_key", ""),
                "s3_prefix": chunk.get("s3_prefix", ""),
                "sp_library": inner_meta.get("sp_library", ""),
                "file_type": inner_meta.get("file_type", ""),
            },
        }

    def export_batch(
        self, chunks: list[dict],
    ) -> list[tuple[str, dict]]:
        """Export a batch of chunks as ``(text, metadata_dict)`` tuples.

        Suitable for writing alongside chunk text files for Bedrock KB
        data source ingestion.
        """
        return [
            (chunk.get("text", ""), self.export_chunk_metadata(chunk))
            for chunk in chunks
        ]
