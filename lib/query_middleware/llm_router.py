"""LLM complexity router — select Bedrock model based on query characteristics.

Routes queries to Haiku (fast/cheap), Sonnet (balanced), or Opus (complex)
based on query length and the number of retrieved chunks.
"""

from __future__ import annotations


class LLMRouter:
    """Select a Bedrock model ID based on query complexity."""

    HAIKU = "anthropic.claude-3-haiku-20240307-v1:0"
    SONNET = "anthropic.claude-3-sonnet-20240229-v1:0"
    OPUS = "anthropic.claude-3-opus-20240229-v1:0"

    def select_model(
        self,
        query_text: str,
        chunk_count: int = 0,
        complexity_hint: str = "auto",
    ) -> str:
        """Return the Bedrock model ID to use for this query.

        Parameters
        ----------
        query_text:
            The user's natural-language query.
        chunk_count:
            Number of retrieved KB chunks (0 if not yet known).
        complexity_hint:
            ``"simple"`` -> Haiku, ``"complex"`` -> Opus, ``"auto"`` ->
            heuristic based on query length and chunk count.
        """
        if complexity_hint == "simple":
            return self.HAIKU
        if complexity_hint == "complex":
            return self.OPUS

        query_len = len(query_text)

        if query_len < 100 and chunk_count <= 3:
            return self.HAIKU
        if query_len > 500 or chunk_count > 7:
            return self.OPUS
        return self.SONNET
