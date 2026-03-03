# Permission-Filtered Query Middleware — Design

> **Prompt 5 of 8** — Permission-Filtered Query Middleware

**Goal:** Build a query middleware layer that intercepts every RAG query and applies permission filtering BEFORE any document chunks are retrieved, ensuring the LLM never sees unauthorized content.

**Approach:** Two-step Bedrock KB Retrieve + InvokeModel. A Lambda behind API Gateway resolves user permissions, constructs metadata filters for Bedrock KB's Retrieve API, then passes authorized chunks to Claude via InvokeModel.

---

## Architecture

```
User (Open WebUI / API Gateway)
    │
    │  POST /query
    │  { query_text, user_id, user_groups }
    │
    ▼
┌─────────────────────────────────────┐
│  query_middleware Lambda             │
│                                     │
│  1. Resolve groups                  │
│     ├─ SAML user_groups (input)     │
│     ├─ DynamoDB user-group-cache    │
│     └─ Merge + deduplicate          │
│                                     │
│  2. Get sensitivity ceiling         │
│     └─ PermissionClient             │
│                                     │
│  3. Build Bedrock KB filter         │
│     ├─ listContains(allowed_groups) │
│     └─ sensitivity_level <= ceiling │
│                                     │
│  4. Retrieve (filtered)             │
│     └─ bedrock-agent-runtime:       │
│        Retrieve with filter         │
│                                     │
│  5. Check results                   │
│     ├─ Has chunks → step 6          │
│     └─ No chunks → denial response  │
│                                     │
│  6. Generate (InvokeModel)          │
│     └─ bedrock-runtime:InvokeModel  │
│        Claude with authorized chunks│
│                                     │
│  7. Audit log (CloudWatch JSON)     │
│                                     │
│  8. Return response                 │
└─────────────────────────────────────┘
```

**Key decisions:**
- Middleware is the ONLY path to the vector store — no direct Bedrock KB access
- Permission filters applied at vector search level via Bedrock KB `filter` parameter
- Sensitivity levels stored as integers for numeric comparison
- Group resolution merges SAML assertion + DynamoDB cache (handles SAML size limits)

---

## Module Structure

```
lib/query_middleware/
├── __init__.py
├── client.py          # QueryMiddleware — main orchestrator
├── group_resolver.py  # Merges SAML groups + DynamoDB cache
├── filter_builder.py  # Constructs Bedrock KB RetrievalFilter
├── audit_logger.py    # Structured JSON audit logging
└── response_handler.py # Permission denial + response formatting

tests/
└── test_query_middleware.py
```

**Components:**

1. **GroupResolver** — Takes `user_id` + SAML `user_groups`, calls `PermissionClient.get_user_groups()`, merges, deduplicates.

2. **FilterBuilder** — Constructs Bedrock KB `RetrievalFilter` dict. Uses `orAll` of `listContains` on `allowed_groups` + `lessThanOrEquals` on `sensitivity_level_numeric`.

3. **AuditLogger** — Structured JSON to CloudWatch. Every query logs: timestamp, user_id, user_upn, resolved_groups, filters_applied, chunk_ids, document_ids, sensitivity_levels, query_text_hash (SHA-256), latency_ms, result_type.

4. **ResponseHandler** — Formats LLM responses with citations. Privacy-safe denial for no-results.

5. **QueryMiddleware** — Orchestrator wiring everything together. Single `query()` entry point.

---

## Sensitivity Level Encoding

| Level | Numeric | Description |
|---|---|---|
| `public` | 0 | Anyone |
| `internal` | 1 | All staff |
| `confidential` | 2 | Specific groups |
| `restricted` | 3 | Highly restricted |

Chunks carry both string and numeric values. Bedrock KB filters use numeric for `lessThanOrEquals`.

---

## Chunk Metadata Schema (Bedrock KB sidecar)

```json
{
  "metadataAttributes": {
    "allowed_groups": ["grp-hr-1", "grp-hr-2"],
    "sensitivity_level": "confidential",
    "sensitivity_level_numeric": 2,
    "document_id": "abc123...",
    "s3_prefix": "source/Dynamo/HR",
    "source_s3_key": "source/Dynamo/HR/handbook.pdf",
    "sp_library": "HR",
    "file_type": ".pdf"
  }
}
```

---

## Filter Construction Example

User in groups `["grp-hr-1", "grp-finance-1"]` with ceiling `confidential` (2):

```json
{
  "andAll": [
    {
      "orAll": [
        {"listContains": {"key": "allowed_groups", "value": "grp-hr-1"}},
        {"listContains": {"key": "allowed_groups", "value": "grp-finance-1"}}
      ]
    },
    {
      "lessThanOrEquals": {"key": "sensitivity_level_numeric", "value": 2}
    }
  ]
}
```

---

## Permission Denial Handling

Three result states, but user sees only two messages:

1. **`success`** — Chunks found, LLM response with citations.
2. **`no_results`** (covers both "no semantic match" and "permissions filtered all results"):
   > "I don't have information on that topic in the documents available to you. You may want to check with the relevant department for access to additional resources."

Privacy constraint: response never reveals existence of restricted documents. No words like "restricted", "access denied", "permission".

---

## Response Format

```json
{
  "response_text": "Based on the available documents...",
  "citations": [
    {
      "chunk_id": "abc123_0",
      "document_id": "abc123",
      "source_s3_key": "source/Dynamo/HR/handbook.pdf",
      "text_excerpt": "...",
      "score": 0.87
    }
  ],
  "result_type": "success",
  "chunks_retrieved": 3
}
```

---

## Testing Strategy

Unit tests with mocked Bedrock + DynamoDB:

- **GroupResolver:** cache hit/miss, merge, dedup, expired cache
- **FilterBuilder:** single/multiple groups, sensitivity ceiling, combined filter, empty groups
- **ResponseHandler:** success with citations, safe denial, no leaky language
- **QueryMiddleware:** full flow success, full flow denial, audit log emission
- **AuditLogger:** all fields present, query text hashed, result_type distinct

**Test users:**
- `test_finance_user` — SG-Finance only
- `test_hr_user` — SG-HR only
- `test_contracts_finance_user` — SG-Contracts + SG-Finance
- `test_executive_user` — SG-Executive + SG-Finance + SG-HR
- `test_general_user` — SG-AllStaff only
