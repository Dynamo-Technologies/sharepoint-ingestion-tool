"""Tests for the permission-filtered query middleware."""

import json
import hashlib
import logging
import time
from unittest.mock import patch, MagicMock

import pytest

from lib.query_middleware.group_resolver import GroupResolver
from lib.query_middleware.filter_builder import FilterBuilder
from lib.query_middleware.audit_logger import AuditLogger
from lib.query_middleware.response_handler import ResponseHandler
from lib.query_middleware.client import QueryMiddleware


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
        mock_client.get_user_sensitivity_ceiling.return_value = "internal"
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
        """No SAML groups and no cache -> empty groups list."""
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

    def test_sensitivity_ceiling_from_permission_client(self):
        """Sensitivity ceiling is fetched from PermissionClient."""
        resolver, mock_client = self._make_resolver(
            cache_result=_make_user_group_result(cache_hit=True)
        )
        mock_client.get_user_sensitivity_ceiling.return_value = "confidential"
        result = resolver.resolve("user-001", saml_groups=[])

        assert result.sensitivity_ceiling == "confidential"


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

    def test_result_type_logged_distinctly(self, caplog):
        """Different result_type values are faithfully preserved in the log."""
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
        """Happy path: chunks returned -> LLM response with citations."""
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
        """No chunks -> privacy-safe denial, no LLM call."""
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
