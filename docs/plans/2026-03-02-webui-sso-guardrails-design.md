# Open WebUI SSO Integration & Bedrock Guardrails — Design

> **Prompt 7 of 8** — Open WebUI SSO Integration and Bedrock Guardrails

**Goal:** Wire up the user-facing layer: Open WebUI on ECS Fargate with Entra ID OIDC authentication via ALB, a Lambda-backed API Gateway for permission-filtered RAG queries, an LLM complexity router, and Bedrock Guardrails as defense-in-depth.

---

## Architecture

```
User (browser)
    │
    │  HTTPS
    ▼
ALB (OIDC auth → Entra ID)
    │  Authenticated: injects x-amzn-oidc-data JWT
    ▼
ECS Fargate (Open WebUI container)
    │  Forwards auth headers to backend API
    │  RAG_API_URL = https://{api-gw-id}.execute-api.{region}.amazonaws.com
    ▼
API Gateway (HTTP API)
    │
    ├── POST /query         → Lambda Authorizer → query-handler Lambda
    ├── GET  /health        → (no auth) → query-handler Lambda
    └── GET  /user/permissions → Lambda Authorizer → query-handler Lambda
                                    │
                                    ├── Auth extraction (OIDC JWT → user_id + groups)
                                    ├── LLM Router (Haiku / Sonnet / Opus selection)
                                    ├── QueryMiddleware (permission-filtered retrieval)
                                    └── Bedrock Guardrails (PII, topics, content)
```

**Key decisions:**
- ALB handles OIDC auth natively — no custom auth code for the login flow
- No custom domain initially — uses ALB auto-generated DNS + API Gateway URL
- API Gateway uses a Lambda authorizer that validates the forwarded OIDC JWT
- Single query-handler Lambda handles all three routes (POST /query, GET /health, GET /user/permissions)
- Bedrock Guardrails applied as a guardrail ID parameter on every InvokeModel call

---

## Components

### 1. ECS Fargate + ALB (Open WebUI)

**ALB Configuration:**
- Internet-facing ALB in a new VPC (public subnets in 2 AZs)
- OIDC authentication action on the HTTPS listener
  - Entra ID app registration provides: client_id, client_secret, issuer URL
  - ALB redirects unauthenticated users to `login.microsoftonline.com/{tenant}/v2.0`
  - On success, ALB sets `x-amzn-oidc-data` (JWT), `x-amzn-oidc-accesstoken`, `x-amzn-oidc-identity`
- HTTP listener redirects to HTTPS
- Health check: `GET /health` on the Open WebUI container (port 8080)
- ACM certificate: not created (no custom domain) — ALB uses HTTP for now with OIDC requiring HTTPS, so we'll create a self-signed or use ACM with ALB DNS validation

**Note on HTTPS:** ALB OIDC authentication requires an HTTPS listener. Without a custom domain, we'll create an ACM certificate for the ALB and use the ALB's DNS name. However, ACM can't issue certs for `*.elb.amazonaws.com` domains. Two options:
1. Use the ALB on HTTP only and skip ALB-native OIDC — instead use API Gateway authorizer for auth
2. Require a custom domain

**Revised approach:** Since we don't have a domain yet, the ALB will forward traffic without OIDC auth to the Open WebUI container. Authentication will be enforced at the **API Gateway layer** using a Lambda authorizer. Open WebUI will be configured to require login (its built-in auth) and pass an API key or bearer token to the backend. The OIDC flow will be added when a custom domain is available.

**For now:** ALB → Open WebUI (HTTP, port 8080). API Gateway handles all authentication via Lambda authorizer. Open WebUI is configured with `RAG_API_URL` pointing to the API Gateway.

**ECS Task Definition:**
- Container image: from `equinsu-ocha` repo (Open WebUI fork)
- Port: 8080
- Memory: 1024 MB, CPU: 512 (0.5 vCPU)
- Environment variables:
  - `RAG_API_BASE_URL`: API Gateway URL
  - `WEBUI_AUTH`: `true`
  - `WEBUI_SECRET_KEY`: from Secrets Manager

**Networking:**
- New VPC: 10.100.0.0/16
- 2 public subnets (for ALB): 10.100.1.0/24, 10.100.2.0/24
- 2 private subnets (for ECS tasks): 10.100.10.0/24, 10.100.11.0/24
- NAT Gateway in public subnet (for ECS tasks to reach AWS APIs)
- Security groups: ALB allows 80/443 inbound; ECS allows 8080 from ALB only

### 2. API Gateway (HTTP API) + Lambda Authorizer

**API Gateway:**
- Type: HTTP API (cheaper, faster than REST API)
- Routes:
  - `POST /query` → query-handler Lambda (authorized)
  - `GET /health` → query-handler Lambda (no auth)
  - `GET /user/permissions` → query-handler Lambda (authorized)
- CORS enabled for Open WebUI origin
- Throttling: 100 requests/second burst, 50 sustained per route
- CloudWatch access logging enabled

**Lambda Authorizer:**
- Type: REQUEST authorizer (examines headers)
- Extracts `Authorization: Bearer <token>` header
- Validates JWT:
  - For ALB-issued tokens: verifies against ALB's public key (from `x-amzn-oidc-data`)
  - For API keys: looks up in a simple API key table or env var
- Extracts claims: `sub` (user_id), `groups`, `email` (UPN)
- Returns IAM policy allowing/denying the route
- Caches authorization for 300 seconds

**Auth extraction module** (`lib/auth/token_validator.py`):
- Parses JWT from Authorization header
- Downloads ALB signing keys from AWS regional endpoint
- Validates: signature, expiry, issuer, audience
- Returns `AuthenticatedUser(user_id, upn, groups)`

### 3. Query Handler Lambda (`src/query_handler.py`)

Single Lambda handling all API Gateway routes:

- `POST /query`:
  1. Extract user context from authorizer
  2. Parse request body: `{"query": "...", "complexity_hint": "auto|simple|complex"}`
  3. Run LLM Router to select model
  4. Call `QueryMiddleware.query(query_text, user_id, user_groups)` with selected model
  5. Return response with citations

- `GET /health`:
  1. Return `{"status": "healthy", "version": "1.0.0"}`

- `GET /user/permissions`:
  1. Extract user context from authorizer
  2. Call `GroupResolver.resolve(user_id, saml_groups)`
  3. Return `{"user_id": ..., "upn": ..., "groups": [...], "sensitivity_ceiling": ...}`

### 4. LLM Router (`lib/query_middleware/llm_router.py`)

Simple model selection based on query complexity:

```python
class LLMRouter:
    HAIKU = "anthropic.claude-3-haiku-20240307-v1:0"
    SONNET = "anthropic.claude-3-sonnet-20240229-v1:0"
    OPUS = "anthropic.claude-3-opus-20240229-v1:0"

    def select_model(self, query_text, chunk_count=0, complexity_hint="auto"):
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
```

### 5. Bedrock Guardrails

**Terraform resource** (`aws_bedrock_guardrail`):

- **PII detection**: Block/redact SSNs, personal addresses, salary/compensation, phone numbers, credit card numbers
- **Topic blocking**: Deny generation of personal medical advice, legal advice, investment/financial advice
- **Content filtering**: Block harmful, hateful, sexual, violent content at HIGH threshold
- **Grounding**: Encourage source citation, set grounding threshold

**Integration:** The guardrail ID is passed to `bedrock-runtime.invoke_model()` via the `guardrailIdentifier` and `guardrailVersion` parameters. The QueryMiddleware is updated to accept an optional `guardrail_id` parameter.

### 6. Infrastructure (Terraform)

**New files:**
- `terraform/vpc.tf` — VPC, subnets, NAT Gateway, Internet Gateway, route tables
- `terraform/ecs.tf` — ECS cluster, task definition, service, ALB, target group, listeners
- `terraform/api_gateway.tf` — HTTP API, routes, Lambda integration, authorizer
- `terraform/lambda_api.tf` — Query handler Lambda + authorizer Lambda
- `terraform/iam_api.tf` — IAM roles for API Lambdas
- `terraform/guardrails.tf` — Bedrock Guardrail resource

**New variables:**
- `open_webui_image` — ECR image URI for Open WebUI
- `oidc_client_id` — Entra ID app registration client ID (for future OIDC)
- `oidc_client_secret` — Entra ID app registration client secret
- `knowledge_base_id` — Bedrock Knowledge Base ID
- `bedrock_model_id` — Default Bedrock model ID

---

## Module Structure

```
lib/auth/
├── __init__.py
├── token_validator.py     # JWT parsing + validation
└── models.py              # AuthenticatedUser dataclass

lib/query_middleware/
├── llm_router.py          # Model complexity router (new)
├── client.py              # QueryMiddleware (updated: guardrail_id param)
└── (existing files unchanged)

src/
├── query_handler.py       # API Gateway Lambda handler (POST /query, GET /health, GET /user/permissions)
└── api_authorizer.py      # Lambda authorizer (JWT validation → IAM policy)

tests/
├── test_token_validator.py
├── test_llm_router.py
├── test_query_handler.py
├── test_api_authorizer.py
└── test_api_e2e.py        # End-to-end authenticated query flow

terraform/
├── vpc.tf
├── ecs.tf
├── api_gateway.tf
├── lambda_api.tf
├── iam_api.tf
└── guardrails.tf
```

---

## Testing Strategy

- **token_validator**: Mock JWTs with valid/expired/invalid signatures, missing claims
- **llm_router**: Parameterized tests for query length/chunk count thresholds, explicit hints
- **query_handler**: Mock QueryMiddleware + GroupResolver, test all 3 routes, test unauthorized access
- **api_authorizer**: Mock JWT validation, test allow/deny policies, missing headers
- **E2E**: Authenticated user → POST /query → permission-filtered retrieval → model-routed response → guardrails applied → response returned. Also: unauthorized query returns privacy-safe denial (not error, not leak).

---

## Data Flow: Authenticated Query

```
1. User opens Open WebUI → ALB forwards to ECS container
2. User types query → Open WebUI POSTs to API Gateway /query
   Headers: Authorization: Bearer <token>
   Body: {"query": "What are our Q4 revenue targets?"}
3. API Gateway invokes Lambda Authorizer
   → Validates JWT, extracts user_id + groups
   → Returns IAM Allow policy with user context
4. API Gateway invokes query-handler Lambda
   → Extracts user context from authorizer
   → LLM Router selects Sonnet (medium complexity)
   → QueryMiddleware.query("What are...", user_id, groups)
     → GroupResolver merges SAML + cached groups
     → FilterBuilder creates Bedrock filter
     → Bedrock KB Retrieve (filtered)
     → InvokeModel with guardrail_id (PII redacted, topics blocked)
   → Returns response with citations
5. Open WebUI displays answer + source citations
```
