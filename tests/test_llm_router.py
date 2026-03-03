"""Tests for LLM Router — model selection by query complexity."""

from __future__ import annotations

import pytest

from lib.query_middleware.llm_router import LLMRouter


class TestLLMRouter:
    @pytest.fixture
    def router(self):
        return LLMRouter()

    # --- Explicit hints ---

    def test_simple_hint_returns_haiku(self, router):
        model = router.select_model("Any query", complexity_hint="simple")
        assert model == LLMRouter.HAIKU

    def test_complex_hint_returns_opus(self, router):
        model = router.select_model("Any query", complexity_hint="complex")
        assert model == LLMRouter.OPUS

    # --- Auto mode: short query, few chunks -> Haiku ---

    def test_short_query_few_chunks_returns_haiku(self, router):
        model = router.select_model("What is X?", chunk_count=2)
        assert model == LLMRouter.HAIKU

    # --- Auto mode: medium query -> Sonnet ---

    def test_medium_query_returns_sonnet(self, router):
        query = "x" * 200  # 200 chars, between 100 and 500
        model = router.select_model(query, chunk_count=5)
        assert model == LLMRouter.SONNET

    # --- Auto mode: long query -> Opus ---

    def test_long_query_returns_opus(self, router):
        query = "x" * 600  # >500 chars
        model = router.select_model(query, chunk_count=3)
        assert model == LLMRouter.OPUS

    # --- Auto mode: many chunks -> Opus ---

    def test_many_chunks_returns_opus(self, router):
        model = router.select_model("Short query", chunk_count=10)
        assert model == LLMRouter.OPUS

    # --- Boundary conditions ---

    def test_exactly_100_chars_not_haiku(self, router):
        query = "x" * 100  # boundary: >= 100 -> not Haiku
        model = router.select_model(query, chunk_count=3)
        assert model == LLMRouter.SONNET

    def test_exactly_500_chars_not_opus(self, router):
        query = "x" * 500  # boundary: <= 500 -> not Opus
        model = router.select_model(query, chunk_count=5)
        assert model == LLMRouter.SONNET

    def test_chunk_count_4_with_short_query_is_sonnet(self, router):
        model = router.select_model("Short", chunk_count=4)
        assert model == LLMRouter.SONNET

    def test_default_chunk_count_zero(self, router):
        model = router.select_model("Short query")
        assert model == LLMRouter.HAIKU
