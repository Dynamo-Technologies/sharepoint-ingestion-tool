# Permission-Filtered Query Middleware Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a query middleware that intercepts RAG queries, resolves user permissions, applies mandatory filters to Bedrock KB retrieval, and ensures the LLM never sees unauthorized content.

**Architecture:** Two-step Bedrock KB flow. A `QueryMiddleware` orchestrator resolves user groups (SAML + DynamoDB cache), constructs metadata filters for Bedrock KB's Retrieve API (`listContains` on `allowed_groups`, `lessThanOrEquals` on `sensitivity_level_numeric`), then passes authorized chunks to Bedrock InvokeModel (Claude). Privacy-safe denial when no results. Structured JSON audit logging to CloudWatch.

**Tech Stack:** Python 3.11, boto3 (bedrock-agent-runtime, bedrock-runtime), DynamoDB, pytest + moto + unittest.mock

---

## Task 1: GroupResolver — Merge SAML Groups with DynamoDB Cache

**Files:**
- Create: `lib/query_middleware/__init__.py`
- Create: `lib/query_middleware/group_resolver.py`
- Test: `tests/test_query_middleware.py`

**Context:** The `GroupResolver` takes a `user_id` and an optional list of SAML `user_groups`, queries the `user-group-cache` DynamoDB table via the existing `PermissionClient.get_user_groups()`, and returns a merged, deduplicated group list plus user metadata (UPN, custom attributes). It handles: SAML-only, cache-only, merged, and both-empty scenarios.

**Step 1: Write the failing tests**

Create `tests/test_query_middleware.py`:

```python
"""Tests for the permission-filtered query middleware."""

from unittest.mock import MagicMock, patch
from dataclasses import dataclass, field

import pytest

from lib.query_middleware.group_resolver import GroupResolver


# ===================================================================
# Helpers
# ===================================================================

def _make_user_group_result(**overrides):
    """Build a mock UserGroupResult with sensible defaults."""
    defaults = {
        "user_id": "user-001",
        "groups": ["grp-hr-1", "grp-finance-1"],
        "upn": "alice@dynamo.com",
        "custom_attributes": {"ext_ClearanceLevel": "confidential"},
        "last_synced": "2026-03-01T00:00:00Z",
        "source": "scim",
        "cache_hit": True,
        "cache_expired": False,
    }
    defaults.update(overrides)
    obj = MagicMock()
    for k, v in defaults.items():
        setattr(obj, k, v)
    return obj


# ===================================================================
# GroupResolver tests
# ===================================================================

class TestGroupResolver:
    def _make_resolver(self, cache_result=None):
        """Create a GroupResolver with a mocked PermissionClient."""
        mock_client = MagicMock()
        if cache_result is not None:
            mock_client.get_user_groups.return_value = cache_result
        else:
            mock_client.get_user_groups.return_value = _make_user_group_result(
                cache_hit=False, groups=[]
            )
        resolver = GroupResolver(permission_client=mock_client)
        return resolver, mock_client

    def test_saml_groups_only_cache_miss(self):
        """When cache misses, return SAML groups as-is."""
        resolver, mock_client = self._make_resolver(
            cache_result=_make_user_group_result(cache_hit=False, groups=[])
        )
        result = resolver.resolve("user-001", saml_groups=["grp-a", "grp-b"])

        assert set(result.groups) == {"grp-a", "grp-b"}
        assert result.cache_hit is False

    def test_cache_hit_merges_with_saml(self):
        """When cache hits, merge SAML + cache groups and deduplicate."""
        resolver, _ = self._make_resolver(
            cache_result=_make_user_group_result(
                groups=["grp-hr-1", "grp-finance-1"], cache_hit=True
            )
        )
        result = resolver.resolve("user-001", saml_groups=["grp-hr-1", "grp-new"])

        assert "grp-hr-1" in result.groups
        assert "grp-finance-1" in result.groups
        assert "grp-new" in result.groups
        # No duplicates
        assert len(result.groups) == len(set(result.groups))

    def test_cache_expired_still_uses_groups(self):
        """Expired cache still returns groups but flags cache_expired."""
        resolver, _ = self._make_resolver(
            cache_result=_make_user_group_result(
                groups=["grp-old"], cache_hit=True, cache_expired=True
            )
        )
        result = resolver.resolve("user-001", saml_groups=[])

        assert "grp-old" in result.groups
        assert result.cache_expired is True

    def test_empty_saml_falls_back_to_cache(self):
        """With no SAML groups, use cache groups only."""
        resolver, _ = self._make_resolver(
            cache_result=_make_user_group_result(groups=["grp-cached"], cache_hit=True)
        )
        result = resolver.resolve("user-001", saml_groups=[])

        assert result.groups == ["grp-cached"]

    def test_both_empty_returns_empty(self):
        """No SAML groups and no cache → empty groups list."""
        resolver, _ = self._make_resolver(
            cache_result=_make_user_group_result(
                cache_hit=False, groups=[]
            )
        )
        result = resolver.resolve("user-001", saml_groups=[])

        assert result.groups == []

    def test_upn_from_cache(self):
        """UPN is taken from cache result when available."""
        resolver, _ = self._make_resolver(
            cache_result=_make_user_group_result(upn="alice@dynamo.com", cache_hit=True)
        )
        result = resolver.resolve("user-001", saml_groups=[])

        assert result.upn == "alice@dynamo.com"

    def test_custom_attributes_from_cache(self):
        """Custom attributes are taken from cache result."""
        resolver, _ = self._make_resolver(
            cache_result=_make_user_group_result(
                custom_attributes={"ext_ClearanceLevel": "confidential"},
                cache_hit=True,
            )
        )
        result = resolver.resolve("user-001", saml_groups=[])

        assert result.custom_attributes == {"ext_ClearanceLevel": "confidential"}
```

Create `lib/query_middleware/__init__.py`:

```python
"""Permission-filtered query middleware for the RAG pipeline.

Intercepts every query, resolves user permissions from SAML assertions
and DynamoDB cache, applies mandatory filters to Bedrock KB retrieval,
and ensures the LLM never sees unauthorized content.
"""
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_query_middleware.py::TestGroupResolver -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lib.query_middleware'`

**Step 3: Write minimal implementation**

Create `lib/query_middleware/group_resolver.py`:

```python
"""Resolve a user's full group list from SAML assertion + DynamoDB cache.

Merges groups from the SAML assertion (passed at query time) with cached
groups from the ``user-group-cache`` DynamoDB table (synced via SCIM).
Deduplicates and returns a unified result with user metadata.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from lib.dynamo_permissions.client import PermissionClient

logger = logging.getLogger(__name__)


@dataclass
class ResolvedUser:
    """Result of resolving a user's groups and metadata."""

    user_id: str
    groups: list[str]
    upn: str = ""
    custom_attributes: dict[str, str] = field(default_factory=dict)
    sensitivity_ceiling: str = "internal"
    cache_hit: bool = False
    cache_expired: bool = False


class GroupResolver:
    """Merges SAML assertion groups with DynamoDB-cached groups."""

    def __init__(self, permission_client: PermissionClient | None = None) -> None:
        self._client = permission_client or PermissionClient()

    def resolve(
        self,
        user_id: str,
        saml_groups: list[str] | None = None,
    ) -> ResolvedUser:
        """Resolve the user's full group list.

        Parameters
        ----------
        user_id:
            Entra ID User Object ID.
        saml_groups:
            Group Object IDs from the SAML assertion.  May be ``None``
            or empty if the assertion was not available.

        Returns
        -------
        ResolvedUser
            Merged, deduplicated group list with user metadata.
        """
        saml_groups = saml_groups or []

        # Look up cached groups
        cache_result = self._client.get_user_groups(user_id)

        # Merge and deduplicate
        all_groups = list(set(saml_groups) | set(cache_result.groups))

        # Get sensitivity ceiling
        ceiling = self._client.get_user_sensitivity_ceiling(user_id)

        return ResolvedUser(
            user_id=user_id,
            groups=sorted(all_groups),
            upn=cache_result.upn,
            custom_attributes=dict(cache_result.custom_attributes),
            sensitivity_ceiling=ceiling,
            cache_hit=cache_result.cache_hit,
            cache_expired=cache_result.cache_expired,
        )
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_query_middleware.py::TestGroupResolver -v`
Expected: All 8 PASS

**Step 5: Commit**

```bash
git add lib/query_middleware/__init__.py lib/query_middleware/group_resolver.py tests/test_query_middleware.py
git commit -m "feat: add GroupResolver for SAML + DynamoDB group merging"
```

---

## Task 2: FilterBuilder — Construct Bedrock KB RetrievalFilter

**Files:**
- Create: `lib/query_middleware/filter_builder.py`
- Modify: `tests/test_query_middleware.py` (add FilterBuilder tests)

**Context:** The `FilterBuilder` takes a resolved group list and sensitivity ceiling and produces the Bedrock KB `RetrievalFilter` dict. It uses `orAll` of `listContains` on `allowed_groups` combined via `andAll` with `lessThanOrEquals` on `sensitivity_level_numeric`. The sensitivity level strings are mapped to integers: public=0, internal=1, confidential=2, restricted=3.

**Step 1: Write the failing tests**

Add to `tests/test_query_middleware.py`:

```python
from lib.query_middleware.filter_builder import FilterBuilder


# ===================================================================
# FilterBuilder tests
# ===================================================================

class TestFilterBuilder:
    def test_single_group_produces_list_contains(self):
        """One group → single listContains (no orAll wrapper needed)."""
        builder = FilterBuilder()
        f = builder.build_filter(groups=["grp-hr-1"], sensitivity_ceiling="confidential")

        # Should have andAll with group filter + sensitivity filter
        assert "andAll" in f
        conditions = f["andAll"]
        assert len(conditions) == 2

    def test_multiple_groups_produces_or_all(self):
        """Multiple groups → orAll of listContains entries."""
        builder = FilterBuilder()
        f = builder.build_filter(
            groups=["grp-hr-1", "grp-finance-1"],
            sensitivity_ceiling="confidential",
        )

        and_conditions = f["andAll"]
        # First condition is the group filter (orAll)
        group_filter = and_conditions[0]
        assert "orAll" in group_filter
        list_contains = group_filter["orAll"]
        assert len(list_contains) == 2

        # Each should be a listContains
        for lc in list_contains:
            assert "listContains" in lc
            assert lc["listContains"]["key"] == "allowed_groups"

        values = {lc["listContains"]["value"] for lc in list_contains}
        assert values == {"grp-hr-1", "grp-finance-1"}

    def test_sensitivity_ceiling_maps_to_numeric(self):
        """Sensitivity ceiling is converted to numeric for lessThanOrEquals."""
        builder = FilterBuilder()

        for level, expected_num in [
            ("public", 0),
            ("internal", 1),
            ("confidential", 2),
            ("restricted", 3),
        ]:
            f = builder.build_filter(groups=["grp-a"], sensitivity_ceiling=level)
            sensitivity_filter = f["andAll"][1]
            assert sensitivity_filter["lessThanOrEquals"]["key"] == "sensitivity_level_numeric"
            assert sensitivity_filter["lessThanOrEquals"]["value"] == expected_num

    def test_combined_filter_structure(self):
        """Full filter has andAll wrapping [group_filter, sensitivity_filter]."""
        builder = FilterBuilder()
        f = builder.build_filter(
            groups=["grp-hr-1", "grp-finance-1"],
            sensitivity_ceiling="confidential",
        )

        assert "andAll" in f
        assert len(f["andAll"]) == 2

        # Group filter
        group_filter = f["andAll"][0]
        assert "orAll" in group_filter

        # Sensitivity filter
        sens_filter = f["andAll"][1]
        assert "lessThanOrEquals" in sens_filter
        assert sens_filter["lessThanOrEquals"]["value"] == 2

    def test_empty_groups_returns_impossible_filter(self):
        """No groups → filter that matches nothing (empty orAll)."""
        builder = FilterBuilder()
        f = builder.build_filter(groups=[], sensitivity_ceiling="internal")

        # With no groups, the group filter should ensure nothing matches.
        # We use a listContains with a UUID that no document will have.
        and_conditions = f["andAll"]
        group_filter = and_conditions[0]
        assert "listContains" in group_filter
        assert group_filter["listContains"]["value"] == "__no_access__"

    def test_unknown_sensitivity_defaults_to_public(self):
        """Unknown sensitivity string defaults to public (0)."""
        builder = FilterBuilder()
        f = builder.build_filter(groups=["grp-a"], sensitivity_ceiling="unknown_level")

        sens_filter = f["andAll"][1]
        assert sens_filter["lessThanOrEquals"]["value"] == 0
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_query_middleware.py::TestFilterBuilder -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write minimal implementation**

Create `lib/query_middleware/filter_builder.py`:

```python
"""Construct Bedrock Knowledge Base RetrievalFilter dicts.

Translates resolved user groups and sensitivity ceiling into the filter
format expected by the Bedrock ``Retrieve`` API's
``vectorSearchConfiguration.filter`` parameter.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Map sensitivity level strings to integers for numeric comparison.
SENSITIVITY_MAP: dict[str, int] = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
    "restricted": 3,
}


class FilterBuilder:
    """Build Bedrock KB retrieval filters from user permissions."""

    def build_filter(
        self,
        groups: list[str],
        sensitivity_ceiling: str,
    ) -> dict:
        """Construct a RetrievalFilter dict.

        Parameters
        ----------
        groups:
            Merged, deduplicated list of the user's group IDs.
        sensitivity_ceiling:
            Maximum sensitivity level the user may access.

        Returns
        -------
        dict
            A Bedrock KB ``RetrievalFilter`` dict ready to pass to the
            ``Retrieve`` API.
        """
        group_filter = self._build_group_filter(groups)
        sensitivity_filter = self._build_sensitivity_filter(sensitivity_ceiling)

        return {
            "andAll": [group_filter, sensitivity_filter],
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_group_filter(groups: list[str]) -> dict:
        """Build the group membership filter.

        Uses ``listContains`` on ``allowed_groups`` — the chunk's
        ``allowed_groups`` list must contain at least one of the user's
        groups.
        """
        if not groups:
            # No groups → impossible filter (matches nothing)
            return {"listContains": {"key": "allowed_groups", "value": "__no_access__"}}

        if len(groups) == 1:
            return {"listContains": {"key": "allowed_groups", "value": groups[0]}}

        return {
            "orAll": [
                {"listContains": {"key": "allowed_groups", "value": g}}
                for g in groups
            ],
        }

    @staticmethod
    def _build_sensitivity_filter(ceiling: str) -> dict:
        """Build the sensitivity ceiling filter.

        Uses ``lessThanOrEquals`` on ``sensitivity_level_numeric``.
        """
        numeric = SENSITIVITY_MAP.get(ceiling.lower(), 0)
        return {
            "lessThanOrEquals": {
                "key": "sensitivity_level_numeric",
                "value": numeric,
            },
        }
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_query_middleware.py::TestFilterBuilder -v`
Expected: All 6 PASS

**Step 5: Commit**

```bash
git add lib/query_middleware/filter_builder.py tests/test_query_middleware.py
git commit -m "feat: add FilterBuilder for Bedrock KB permission filters"
```

---

## Task 3: AuditLogger — Structured JSON Query Logging

**Files:**
- Create: `lib/query_middleware/audit_logger.py`
- Modify: `tests/test_query_middleware.py` (add AuditLogger tests)

**Context:** Every query logs a structured JSON record to CloudWatch. Fields: timestamp, user_id, user_upn, resolved_groups, permission_filters_applied, chunk_ids_retrieved, source_document_ids, sensitivity_levels_of_retrieved_chunks, query_text_hash (SHA-256, NOT the full query), response_latency_ms, result_type. The `permission_scoped_null` result type is tagged distinctly.

**Step 1: Write the failing tests**

Add to `tests/test_query_middleware.py`:

```python
import json
import hashlib
import logging

from lib.query_middleware.audit_logger import AuditLogger


# ===================================================================
# AuditLogger tests
# ===================================================================

class TestAuditLogger:
    def test_log_entry_has_required_fields(self, caplog):
        """Audit log must contain all required fields."""
        logger_instance = AuditLogger()

        with caplog.at_level(logging.INFO, logger="query_middleware.audit"):
            logger_instance.log_query(
                user_id="user-001",
                user_upn="alice@dynamo.com",
                resolved_groups=["grp-hr-1"],
                filters_applied={"andAll": []},
                chunk_ids=["abc_0", "abc_1"],
                document_ids=["abc"],
                sensitivity_levels=["confidential"],
                query_text="What is the PTO policy?",
                latency_ms=150,
                result_type="success",
            )

        assert len(caplog.records) == 1
        entry = json.loads(caplog.records[0].message)

        required_fields = {
            "timestamp", "user_id", "user_upn", "resolved_groups",
            "filters_applied", "chunk_ids_retrieved", "source_document_ids",
            "sensitivity_levels", "query_text_hash", "response_latency_ms",
            "result_type",
        }
        assert required_fields.issubset(set(entry.keys()))

    def test_query_text_is_hashed_not_stored(self, caplog):
        """Query text must be SHA-256 hashed, not stored in plaintext."""
        logger_instance = AuditLogger()
        query = "What is the PTO policy?"

        with caplog.at_level(logging.INFO, logger="query_middleware.audit"):
            logger_instance.log_query(
                user_id="user-001",
                user_upn="",
                resolved_groups=[],
                filters_applied={},
                chunk_ids=[],
                document_ids=[],
                sensitivity_levels=[],
                query_text=query,
                latency_ms=100,
                result_type="no_results",
            )

        entry = json.loads(caplog.records[0].message)

        # Must NOT contain the original query text
        assert query not in json.dumps(entry)

        # Must contain the SHA-256 hash
        expected_hash = hashlib.sha256(query.encode()).hexdigest()
        assert entry["query_text_hash"] == expected_hash

    def test_permission_scoped_null_logged_distinctly(self, caplog):
        """permission_scoped_null result type appears in log."""
        logger_instance = AuditLogger()

        with caplog.at_level(logging.INFO, logger="query_middleware.audit"):
            logger_instance.log_query(
                user_id="user-001",
                user_upn="bob@dynamo.com",
                resolved_groups=["grp-general"],
                filters_applied={"andAll": []},
                chunk_ids=[],
                document_ids=[],
                sensitivity_levels=[],
                query_text="Tell me about HR policies",
                latency_ms=200,
                result_type="no_results",
            )

        entry = json.loads(caplog.records[0].message)
        assert entry["result_type"] == "no_results"

    def test_log_entry_is_valid_json(self, caplog):
        """Log message must be valid JSON (for CloudWatch parsing)."""
        logger_instance = AuditLogger()

        with caplog.at_level(logging.INFO, logger="query_middleware.audit"):
            logger_instance.log_query(
                user_id="user-001",
                user_upn="",
                resolved_groups=[],
                filters_applied={},
                chunk_ids=[],
                document_ids=[],
                sensitivity_levels=[],
                query_text="test",
                latency_ms=50,
                result_type="success",
            )

        # Should not raise
        entry = json.loads(caplog.records[0].message)
        assert isinstance(entry, dict)
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_query_middleware.py::TestAuditLogger -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write minimal implementation**

Create `lib/query_middleware/audit_logger.py`:

```python
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
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_query_middleware.py::TestAuditLogger -v`
Expected: All 4 PASS

**Step 5: Commit**

```bash
git add lib/query_middleware/audit_logger.py tests/test_query_middleware.py
git commit -m "feat: add AuditLogger for structured JSON query audit trail"
```

---

## Task 4: ResponseHandler — Format Responses and Privacy-Safe Denials

**Files:**
- Create: `lib/query_middleware/response_handler.py`
- Modify: `tests/test_query_middleware.py` (add ResponseHandler tests)

**Context:** The `ResponseHandler` formats successful Bedrock InvokeModel responses with citations, and produces privacy-safe denial messages when no chunks are returned. The denial message must NEVER reveal the existence of restricted documents — no words like "restricted", "access", "permission", "denied".

**Step 1: Write the failing tests**

Add to `tests/test_query_middleware.py`:

```python
from lib.query_middleware.response_handler import ResponseHandler


# ===================================================================
# ResponseHandler tests
# ===================================================================

class TestResponseHandler:
    def test_success_response_includes_citations(self):
        """Successful response includes text and citations."""
        handler = ResponseHandler()
        chunks = [
            {
                "content": {"text": "PTO policy allows 15 days..."},
                "metadata": {
                    "chunk_id": "abc_0",
                    "document_id": "abc",
                    "source_s3_key": "source/Dynamo/HR/handbook.pdf",
                    "sensitivity_level": "confidential",
                },
                "score": 0.92,
            },
        ]

        result = handler.format_success(
            llm_response_text="Based on the handbook, PTO is 15 days...",
            chunks=chunks,
        )

        assert result["result_type"] == "success"
        assert result["response_text"] == "Based on the handbook, PTO is 15 days..."
        assert len(result["citations"]) == 1
        assert result["citations"][0]["chunk_id"] == "abc_0"
        assert result["citations"][0]["score"] == 0.92
        assert result["chunks_retrieved"] == 1

    def test_no_results_returns_safe_message(self):
        """No-results response is helpful and privacy-safe."""
        handler = ResponseHandler()
        result = handler.format_no_results()

        assert result["result_type"] == "no_results"
        assert result["chunks_retrieved"] == 0
        assert result["citations"] == []
        assert len(result["response_text"]) > 0

    def test_denial_does_not_reveal_restricted_content(self):
        """The no-results message must NOT hint at restricted documents."""
        handler = ResponseHandler()
        result = handler.format_no_results()

        text = result["response_text"].lower()
        forbidden_words = [
            "restricted", "access", "permission", "denied", "unauthorized",
            "forbidden", "classified", "blocked", "filtered",
        ]
        for word in forbidden_words:
            assert word not in text, f"Response contains forbidden word: '{word}'"

    def test_success_response_structure(self):
        """Success response has required top-level keys."""
        handler = ResponseHandler()
        result = handler.format_success(
            llm_response_text="Answer here.",
            chunks=[],
        )

        required_keys = {"response_text", "citations", "result_type", "chunks_retrieved"}
        assert required_keys == set(result.keys())
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_query_middleware.py::TestResponseHandler -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write minimal implementation**

Create `lib/query_middleware/response_handler.py`:

```python
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
            metadata = chunk.get("metadata", {})
            citations.append({
                "chunk_id": metadata.get("chunk_id", ""),
                "document_id": metadata.get("document_id", ""),
                "source_s3_key": metadata.get("source_s3_key", ""),
                "text_excerpt": chunk.get("content", {}).get("text", "")[:200],
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
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_query_middleware.py::TestResponseHandler -v`
Expected: All 4 PASS

**Step 5: Commit**

```bash
git add lib/query_middleware/response_handler.py tests/test_query_middleware.py
git commit -m "feat: add ResponseHandler with privacy-safe denial messages"
```

---

## Task 5: QueryMiddleware Orchestrator — Wire Everything Together

**Files:**
- Create: `lib/query_middleware/client.py`
- Modify: `lib/query_middleware/__init__.py` (add exports)
- Modify: `tests/test_query_middleware.py` (add orchestrator tests)

**Context:** The `QueryMiddleware` is the main entry point. Its `query()` method: (1) resolves groups via `GroupResolver`, (2) builds the filter via `FilterBuilder`, (3) calls Bedrock KB `Retrieve` with the filter, (4) if chunks returned, calls Bedrock `InvokeModel` with the chunks as context, (5) logs via `AuditLogger`, (6) returns via `ResponseHandler`. All Bedrock calls are mocked in tests.

**Step 1: Write the failing tests**

Add to `tests/test_query_middleware.py`:

```python
import time
from unittest.mock import patch, MagicMock

from lib.query_middleware.client import QueryMiddleware


# ===================================================================
# QueryMiddleware orchestrator tests
# ===================================================================

class TestQueryMiddleware:
    def _make_middleware(
        self,
        retrieve_results=None,
        invoke_model_response=None,
        resolver_result=None,
    ):
        """Create a QueryMiddleware with mocked dependencies."""
        # Mock GroupResolver
        mock_resolver = MagicMock()
        if resolver_result is None:
            resolver_result = MagicMock()
            resolver_result.user_id = "user-001"
            resolver_result.groups = ["grp-hr-1"]
            resolver_result.upn = "alice@dynamo.com"
            resolver_result.custom_attributes = {}
            resolver_result.sensitivity_ceiling = "confidential"
            resolver_result.cache_hit = True
            resolver_result.cache_expired = False
        mock_resolver.resolve.return_value = resolver_result

        # Mock Bedrock clients
        mock_bedrock_agent = MagicMock()
        if retrieve_results is None:
            retrieve_results = {"retrievalResults": []}
        mock_bedrock_agent.retrieve.return_value = retrieve_results

        mock_bedrock_runtime = MagicMock()
        if invoke_model_response is None:
            invoke_model_response = {
                "body": MagicMock(
                    read=MagicMock(return_value=b'{"content": [{"text": "The answer is 42."}]}')
                )
            }
        mock_bedrock_runtime.invoke_model.return_value = invoke_model_response

        middleware = QueryMiddleware(
            knowledge_base_id="test-kb-id",
            model_id="anthropic.claude-3-sonnet-20240229-v1:0",
            group_resolver=mock_resolver,
            bedrock_agent_client=mock_bedrock_agent,
            bedrock_runtime_client=mock_bedrock_runtime,
        )

        return middleware, mock_bedrock_agent, mock_bedrock_runtime, mock_resolver

    def test_full_query_with_results(self):
        """Happy path: chunks returned → LLM response with citations."""
        retrieve_results = {
            "retrievalResults": [
                {
                    "content": {"text": "PTO policy allows 15 days per year."},
                    "metadata": {
                        "chunk_id": "abc_0",
                        "document_id": "abc",
                        "source_s3_key": "source/Dynamo/HR/handbook.pdf",
                        "sensitivity_level": "confidential",
                    },
                    "score": 0.92,
                },
            ],
        }

        middleware, mock_agent, mock_runtime, _ = self._make_middleware(
            retrieve_results=retrieve_results,
        )

        result = middleware.query(
            query_text="What is the PTO policy?",
            user_id="user-001",
            user_groups=["grp-hr-1"],
        )

        assert result["result_type"] == "success"
        assert "response_text" in result
        assert len(result["citations"]) == 1
        mock_agent.retrieve.assert_called_once()
        mock_runtime.invoke_model.assert_called_once()

    def test_full_query_no_results_returns_safe_denial(self):
        """No chunks → privacy-safe denial, no LLM call."""
        middleware, mock_agent, mock_runtime, _ = self._make_middleware(
            retrieve_results={"retrievalResults": []},
        )

        result = middleware.query(
            query_text="What about classified stuff?",
            user_id="user-001",
            user_groups=["grp-general"],
        )

        assert result["result_type"] == "no_results"
        assert result["chunks_retrieved"] == 0
        # Should NOT call InvokeModel when there are no chunks
        mock_runtime.invoke_model.assert_not_called()

    def test_retrieve_called_with_filter(self):
        """Bedrock Retrieve is called with the permission filter."""
        middleware, mock_agent, _, _ = self._make_middleware()

        middleware.query(
            query_text="test query",
            user_id="user-001",
            user_groups=["grp-hr-1"],
        )

        call_kwargs = mock_agent.retrieve.call_args[1]
        assert call_kwargs["knowledgeBaseId"] == "test-kb-id"
        assert "filter" in call_kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]

    def test_audit_log_emitted(self, caplog):
        """Every query emits an audit log entry."""
        middleware, _, _, _ = self._make_middleware()

        with caplog.at_level(logging.INFO, logger="query_middleware.audit"):
            middleware.query(
                query_text="test",
                user_id="user-001",
                user_groups=[],
            )

        # Find the audit log entry
        audit_records = [
            r for r in caplog.records if r.name == "query_middleware.audit"
        ]
        assert len(audit_records) == 1
        entry = json.loads(audit_records[0].message)
        assert entry["user_id"] == "user-001"
        assert "query_text_hash" in entry

    def test_group_resolver_called_with_user_and_saml_groups(self):
        """GroupResolver receives user_id and SAML groups."""
        middleware, _, _, mock_resolver = self._make_middleware()

        middleware.query(
            query_text="test",
            user_id="user-001",
            user_groups=["grp-a", "grp-b"],
        )

        mock_resolver.resolve.assert_called_once_with(
            "user-001", saml_groups=["grp-a", "grp-b"]
        )
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_query_middleware.py::TestQueryMiddleware -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write minimal implementation**

Create `lib/query_middleware/client.py`:

```python
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

        # 1. Resolve groups
        resolved = self._resolver.resolve(user_id, saml_groups=user_groups or [])

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

        # 6. Audit log
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        self._audit.log_query(
            user_id=user_id,
            user_upn=resolved.upn,
            resolved_groups=resolved.groups,
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
        return response_body.get("content", [{}])[0].get("text", "")
```

Update `lib/query_middleware/__init__.py`:

```python
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

__all__ = [
    "QueryMiddleware",
    "GroupResolver",
    "ResolvedUser",
    "FilterBuilder",
    "AuditLogger",
    "ResponseHandler",
]
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_query_middleware.py::TestQueryMiddleware -v`
Expected: All 5 PASS

**Step 5: Commit**

```bash
git add lib/query_middleware/client.py lib/query_middleware/__init__.py tests/test_query_middleware.py
git commit -m "feat: add QueryMiddleware orchestrator for permission-filtered RAG"
```

---

## Task 6: Validation Test Users — Permission Tier Tests

**Files:**
- Modify: `tests/test_query_middleware.py` (add validation test users)

**Context:** The prompt requires specific test users representing different permission tiers to validate filter construction and query behavior. These use mocked Bedrock responses to verify the middleware handles each tier correctly.

**Step 1: Write the validation tests**

Add to `tests/test_query_middleware.py`:

```python
# ===================================================================
# Validation test users — permission tier tests
# ===================================================================

class TestPermissionTierUsers:
    """Validation tests per the Prompt 5 checklist.

    Test users:
    - test_finance_user — SG-Finance only
    - test_hr_user — SG-HR only
    - test_contracts_finance_user — SG-Contracts + SG-Finance
    - test_executive_user — SG-Executive + SG-Finance + SG-HR
    - test_general_user — SG-AllStaff only
    """

    def _make_middleware_for_user(self, groups, ceiling="internal", chunks=None):
        """Create middleware with mocked resolver for a specific user."""
        resolved = MagicMock()
        resolved.user_id = "test-user"
        resolved.groups = groups
        resolved.upn = "test@dynamo.com"
        resolved.custom_attributes = {}
        resolved.sensitivity_ceiling = ceiling
        resolved.cache_hit = True
        resolved.cache_expired = False

        mock_resolver = MagicMock()
        mock_resolver.resolve.return_value = resolved

        mock_bedrock_agent = MagicMock()
        if chunks is None:
            chunks = []
        mock_bedrock_agent.retrieve.return_value = {"retrievalResults": chunks}

        mock_bedrock_runtime = MagicMock()
        mock_bedrock_runtime.invoke_model.return_value = {
            "body": MagicMock(
                read=MagicMock(return_value=b'{"content": [{"text": "Answer."}]}')
            ),
        }

        middleware = QueryMiddleware(
            knowledge_base_id="test-kb",
            group_resolver=mock_resolver,
            bedrock_agent_client=mock_bedrock_agent,
            bedrock_runtime_client=mock_bedrock_runtime,
        )

        return middleware, mock_bedrock_agent

    def test_finance_user_filter(self):
        """Finance user → filter includes SG-Finance group."""
        middleware, mock_agent = self._make_middleware_for_user(
            groups=["SG-Finance"], ceiling="confidential"
        )
        middleware.query("financial data", "finance-user", ["SG-Finance"])

        call_kwargs = mock_agent.retrieve.call_args[1]
        f = call_kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"]

        # Group filter should contain SG-Finance
        group_filter = f["andAll"][0]
        assert group_filter["listContains"]["value"] == "SG-Finance"

    def test_general_user_no_finance_results(self):
        """General user with no Finance chunks → no_results."""
        middleware, _ = self._make_middleware_for_user(
            groups=["SG-AllStaff"], ceiling="internal"
        )
        result = middleware.query("financial data", "general-user", ["SG-AllStaff"])

        assert result["result_type"] == "no_results"
        assert result["chunks_retrieved"] == 0

    def test_general_user_denial_is_safe(self):
        """General user denial message does NOT reveal restricted content."""
        middleware, _ = self._make_middleware_for_user(
            groups=["SG-AllStaff"], ceiling="internal"
        )
        result = middleware.query("financial data", "general-user", ["SG-AllStaff"])

        text = result["response_text"].lower()
        for word in ["restricted", "access", "permission", "denied", "filtered"]:
            assert word not in text

    def test_hr_user_filter(self):
        """HR user → filter includes SG-HR group."""
        middleware, mock_agent = self._make_middleware_for_user(
            groups=["SG-HR"], ceiling="confidential"
        )
        middleware.query("HR personnel data", "hr-user", ["SG-HR"])

        call_kwargs = mock_agent.retrieve.call_args[1]
        f = call_kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"]
        group_filter = f["andAll"][0]
        assert group_filter["listContains"]["value"] == "SG-HR"

    def test_contracts_finance_user_filter(self):
        """Contracts+Finance user → filter includes both groups in orAll."""
        middleware, mock_agent = self._make_middleware_for_user(
            groups=["SG-Contracts", "SG-Finance"], ceiling="confidential"
        )
        middleware.query("awarded contracts", "cf-user", ["SG-Contracts", "SG-Finance"])

        call_kwargs = mock_agent.retrieve.call_args[1]
        f = call_kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"]
        group_filter = f["andAll"][0]
        assert "orAll" in group_filter

        values = {lc["listContains"]["value"] for lc in group_filter["orAll"]}
        assert values == {"SG-Contracts", "SG-Finance"}

    def test_executive_user_filter(self):
        """Executive user → filter includes all 3 groups."""
        middleware, mock_agent = self._make_middleware_for_user(
            groups=["SG-Executive", "SG-Finance", "SG-HR"], ceiling="restricted"
        )
        middleware.query("executive summary", "exec-user", ["SG-Executive", "SG-Finance", "SG-HR"])

        call_kwargs = mock_agent.retrieve.call_args[1]
        f = call_kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"]

        group_filter = f["andAll"][0]
        assert "orAll" in group_filter
        values = {lc["listContains"]["value"] for lc in group_filter["orAll"]}
        assert values == {"SG-Executive", "SG-Finance", "SG-HR"}

        # Sensitivity ceiling = restricted (3)
        sens_filter = f["andAll"][1]
        assert sens_filter["lessThanOrEquals"]["value"] == 3

    def test_executive_sensitivity_ceiling(self):
        """Executive user with restricted ceiling can see all sensitivity levels."""
        middleware, mock_agent = self._make_middleware_for_user(
            groups=["SG-Executive"], ceiling="restricted"
        )
        middleware.query("test", "exec-user", [])

        call_kwargs = mock_agent.retrieve.call_args[1]
        f = call_kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"]
        sens_filter = f["andAll"][1]
        assert sens_filter["lessThanOrEquals"]["value"] == 3  # restricted = max

    def test_finance_user_with_chunks_returns_success(self):
        """Finance user gets chunks → success response."""
        finance_chunk = {
            "content": {"text": "Q3 revenue was $10M..."},
            "metadata": {
                "chunk_id": "fin_0",
                "document_id": "fin-doc",
                "source_s3_key": "source/Dynamo/Finance/q3-report.pdf",
                "sensitivity_level": "confidential",
            },
            "score": 0.88,
        }

        middleware, _ = self._make_middleware_for_user(
            groups=["SG-Finance"], ceiling="confidential", chunks=[finance_chunk]
        )
        result = middleware.query("financial data", "finance-user", ["SG-Finance"])

        assert result["result_type"] == "success"
        assert result["chunks_retrieved"] == 1
        assert result["citations"][0]["chunk_id"] == "fin_0"
```

**Step 2: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_query_middleware.py::TestPermissionTierUsers -v`
Expected: All 8 PASS

**Step 3: Run the full test file**

Run: `.venv/bin/python -m pytest tests/test_query_middleware.py -v`
Expected: All tests PASS (8 GroupResolver + 6 FilterBuilder + 4 AuditLogger + 4 ResponseHandler + 5 QueryMiddleware + 8 PermissionTierUsers = 35 tests)

**Step 4: Commit**

```bash
git add tests/test_query_middleware.py
git commit -m "test: add permission tier validation tests for query middleware"
```

---

## Task 7: Metadata Exporter — Bridge Chunker Output to Bedrock KB Format

**Files:**
- Create: `lib/query_middleware/metadata_exporter.py`
- Modify: `tests/test_query_middleware.py` (add exporter tests)

**Context:** Bedrock Knowledge Bases require `.metadata.json` sidecar files alongside content files. The `MetadataExporter` takes chunk dicts (from `DocumentChunker.chunk_document()`, which already include `allowed_groups`, `sensitivity_level`, `s3_prefix` as top-level fields from Task 6 of Prompt 4) and produces the sidecar format. It also adds `sensitivity_level_numeric` for the `FilterBuilder`'s `lessThanOrEquals` comparison.

**Step 1: Write the failing tests**

Add to `tests/test_query_middleware.py`:

```python
from lib.query_middleware.metadata_exporter import MetadataExporter


# ===================================================================
# MetadataExporter tests
# ===================================================================

class TestMetadataExporter:
    def test_exports_metadata_json(self):
        """Produces Bedrock KB sidecar format with metadataAttributes."""
        exporter = MetadataExporter()
        chunk = {
            "chunk_id": "abc_0",
            "document_id": "abc",
            "source_s3_key": "source/Dynamo/HR/handbook.pdf",
            "filename": "handbook.pdf",
            "allowed_groups": ["grp-hr-1", "grp-hr-2"],
            "sensitivity_level": "confidential",
            "s3_prefix": "source/Dynamo/HR",
            "custom_filters": {},
            "text": "PTO policy allows 15 days...",
            "metadata": {"sp_library": "HR", "file_type": ".pdf"},
        }

        result = exporter.export_chunk_metadata(chunk)

        assert "metadataAttributes" in result
        attrs = result["metadataAttributes"]
        assert attrs["allowed_groups"] == ["grp-hr-1", "grp-hr-2"]
        assert attrs["sensitivity_level"] == "confidential"
        assert attrs["sensitivity_level_numeric"] == 2
        assert attrs["document_id"] == "abc"
        assert attrs["chunk_id"] == "abc_0"
        assert attrs["source_s3_key"] == "source/Dynamo/HR/handbook.pdf"
        assert attrs["s3_prefix"] == "source/Dynamo/HR"
        assert attrs["sp_library"] == "HR"
        assert attrs["file_type"] == ".pdf"

    def test_sensitivity_numeric_mapping(self):
        """Each sensitivity level maps to the correct integer."""
        exporter = MetadataExporter()

        for level, expected in [("public", 0), ("internal", 1), ("confidential", 2), ("restricted", 3)]:
            chunk = {
                "sensitivity_level": level,
                "allowed_groups": [],
                "document_id": "",
                "chunk_id": "",
                "source_s3_key": "",
                "s3_prefix": "",
                "custom_filters": {},
                "metadata": {},
            }
            result = exporter.export_chunk_metadata(chunk)
            assert result["metadataAttributes"]["sensitivity_level_numeric"] == expected

    def test_missing_fields_default_safely(self):
        """Missing chunk fields produce safe defaults."""
        exporter = MetadataExporter()
        chunk = {"metadata": {}}  # Minimal chunk

        result = exporter.export_chunk_metadata(chunk)
        attrs = result["metadataAttributes"]

        assert attrs["allowed_groups"] == []
        assert attrs["sensitivity_level"] == ""
        assert attrs["sensitivity_level_numeric"] == 0
        assert attrs["document_id"] == ""

    def test_export_batch_produces_list(self):
        """Batch export returns a list of (chunk_text, metadata_dict) tuples."""
        exporter = MetadataExporter()
        chunks = [
            {
                "text": "Chunk 1 text.",
                "chunk_id": "a_0",
                "document_id": "a",
                "source_s3_key": "source/a.pdf",
                "allowed_groups": ["grp-1"],
                "sensitivity_level": "internal",
                "s3_prefix": "source/",
                "custom_filters": {},
                "metadata": {},
            },
            {
                "text": "Chunk 2 text.",
                "chunk_id": "b_0",
                "document_id": "b",
                "source_s3_key": "source/b.pdf",
                "allowed_groups": ["grp-2"],
                "sensitivity_level": "confidential",
                "s3_prefix": "source/",
                "custom_filters": {},
                "metadata": {},
            },
        ]

        results = exporter.export_batch(chunks)
        assert len(results) == 2
        assert results[0][0] == "Chunk 1 text."
        assert results[0][1]["metadataAttributes"]["chunk_id"] == "a_0"
        assert results[1][0] == "Chunk 2 text."
        assert results[1][1]["metadataAttributes"]["sensitivity_level_numeric"] == 2
```

**Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_query_middleware.py::TestMetadataExporter -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write minimal implementation**

Create `lib/query_middleware/metadata_exporter.py`:

```python
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
                ),
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
```

**Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_query_middleware.py::TestMetadataExporter -v`
Expected: All 4 PASS

**Step 5: Commit**

```bash
git add lib/query_middleware/metadata_exporter.py tests/test_query_middleware.py
git commit -m "feat: add MetadataExporter for Bedrock KB sidecar format"
```

---

## Task 8: Run Full Test Suite and Validate

**Step 1: Run all query middleware tests**

Run: `.venv/bin/python -m pytest tests/test_query_middleware.py -v`
Expected: All tests PASS (~39 tests)

**Step 2: Run the full project test suite**

Run: `.venv/bin/python -m pytest tests/ --ignore=tests/integration -v --tb=short`
Expected: All tests PASS (plus the 6 pre-existing failures in test_file_converter.py and test_path_mapper.py)

**Step 3: Final commit if any fixes were needed**

```bash
git add -A
git commit -m "chore: query middleware complete — all tests passing"
```
