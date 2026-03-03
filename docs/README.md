# Dynamo SharePoint Ingest

Enterprise document ingestion pipeline that syncs documents from SharePoint Online into AWS,
extracts text content via Amazon Textract (or direct extraction for Office formats), and produces
permission-filtered text twins for Amazon Bedrock Knowledge Base indexing and RAG queries.

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [Data Flow](#data-flow)
- [Lambda Functions](#lambda-functions)
- [DynamoDB Tables](#dynamodb-tables)
- [S3 Bucket Structure](#s3-bucket-structure)
- [EventBridge Schedules](#eventbridge-schedules)
- [Environment Variables](#environment-variables)
- [Monitoring and Alerting](#monitoring-and-alerting)
- [Query API](#query-api)
- [Quick Start](#quick-start)
- [Development](#development)
- [Operational Runbooks](#operational-runbooks)
- [Additional Documentation](#additional-documentation)

---

## Architecture Overview

```
                          SHAREPOINT ONLINE
                                |
                          Microsoft Graph API
                          (delta queries)
                                |
          +---------------------+---------------------+
          |                                           |
          v                                           v
  +-----------------+                        +------------------+
  | sp-ingest-      |                        | EC2 Bulk Loader  |
  | daily-sync      |                        | (initial load,   |
  | (EventBridge    |                        |  optional)       |
  |  24h cron)      |                        +--------+---------+
  +--------+--------+                                 |
           |                                          |
           +------------------+-----------------------+
                              |
                              v
                 +---------------------------+
                 |     S3: source/{Org}/     |
                 |        {Department}/      |
                 +------------+--------------+
                              |
                       S3 PutObject event
                              |
                              v
                 +---------------------------+
                 |  sp-ingest-textract-      |
                 |  trigger                  |
                 |  (routes by file type)    |
                 +-----+------------+-------+
                       |            |
           +-----------+            +-----------+
           |                                    |
           v                                    v
  +------------------+              +------------------------+
  | Amazon Textract  |              | Direct Extraction      |
  | (async, PDF)     |              | DOCX / PPTX / XLSX /   |
  +--------+---------+              | TXT                    |
           |                        +----------+-------------+
      SNS notification                         |
           |                                   |
           v                                   |
  +------------------+                         |
  | sp-ingest-       |                         |
  | textract-complete|                         |
  +--------+---------+                         |
           |                                   |
           +----------------+------------------+
                            |
                            v
               +---------------------------+
               |  S3: twins/{Org}/         |
               |       {Department}/       |
               +------------+--------------+
                            |
                            v
               +---------------------------+
               | Amazon Bedrock            |
               | Knowledge Base            |
               +------------+--------------+
                            |
                            v
               +---------------------------+          +------------------+
               | sp-ingest-query-handler   |<---------| API Gateway      |
               | (POST /query,            |          | + Lambda         |
               |  GET /health,            |          |   Authorizer     |
               |  GET /user/permissions)  |          +--------+---------+
               +---------------------------+                   |
                            |                                  |
                            v                                  v
               +---------------------------+       +------------------+
               | Bedrock Guardrails        |       | Open WebUI       |
               | (PII/content/topic)       |       | (ECS Fargate,    |
               | + LLM Router             |       |  optional)       |
               | (Haiku/Sonnet/Opus)      |       +------------------+
               +---------------------------+

  GOVERNANCE (runs on schedule):
  +---------------------+  +-------------------------+  +---------------------+
  | group-cache-refresh |  | permission-drift-       |  | stale-account-      |
  | (every 15 min)      |  | detector (Sun 02:00)    |  | cleanup (daily 03:00)|
  +---------------------+  +-------------------------+  +---------------------+
  +---------------------+
  | compliance-report   |
  | (1st of month 06:00)|
  +---------------------+
```

---

## Data Flow

1. **Sync** -- `daily-sync` Lambda calls Microsoft Graph API delta queries to discover new,
   changed, and deleted documents in SharePoint. Documents are uploaded to `source/` in S3.
   Delta tokens are persisted in DynamoDB for incremental syncs.

2. **Extract** -- S3 PutObject events trigger `textract-trigger`. PDFs are sent to Amazon
   Textract (async). DOCX, PPTX, XLSX, and TXT files are extracted directly in-Lambda.

3. **Twin** -- On Textract completion (via SNS), `textract-complete` retrieves results and
   writes a JSON text twin to `twins/`. Direct extractions write twins immediately from
   the trigger Lambda.

4. **Index** -- Amazon Bedrock Knowledge Base indexes the text twins for RAG retrieval.

5. **Query** -- Users query via API Gateway. The query handler retrieves permission-scoped
   results from the Knowledge Base, applying Bedrock Guardrails and routing to the
   appropriate LLM model (Haiku/Sonnet/Opus) based on query complexity.

6. **Govern** -- Scheduled Lambdas keep permissions fresh (SCIM sync every 15 min), detect
   drift (weekly), clean up stale accounts (daily), and generate compliance reports (monthly).

---

## Lambda Functions

| Lambda | Trigger | Purpose | Timeout | Memory |
|--------|---------|---------|---------|--------|
| `sp-ingest-daily-sync` | EventBridge cron (daily 07:00 UTC) | Incremental SharePoint sync via Graph API delta queries | 900s | 512 MB |
| `sp-ingest-textract-trigger` | S3 PutObject on `source/` | Routes documents to Textract (PDF) or direct extraction (DOCX/PPTX/XLSX/TXT); quarantines unmapped prefixes | 300s | 1024 MB |
| `sp-ingest-textract-complete` | SNS (Textract job done) | Retrieves Textract results, builds JSON text twin, writes to `twins/` | 300s | 1024 MB |
| `sp-ingest-group-cache-refresh` | EventBridge rate (15 min) | SCIM sync -- reads IAM Identity Center groups/users into DynamoDB cache | 300s | 256 MB |
| `sp-ingest-permission-drift-detector` | EventBridge cron (Sun 02:00 UTC) | Scans S3 prefixes for unmapped permissions, publishes drift report to SNS | 300s | 256 MB |
| `sp-ingest-stale-account-cleanup` | EventBridge cron (daily 03:00 UTC) | Removes deleted/disabled users from the group cache | 300s | 256 MB |
| `sp-ingest-compliance-report` | EventBridge cron (1st of month 06:00 UTC) | Generates monthly compliance report to `governance-reports/` in S3 | 300s | 256 MB |
| `sp-ingest-query-handler` | API Gateway (POST /query, GET /health, GET /user/permissions) | Permission-filtered RAG queries via Bedrock Knowledge Base | 60s | 512 MB |
| `sp-ingest-api-authorizer` | API Gateway Lambda authorizer | JWT and API key validation for query API | 10s | 128 MB |

All Lambdas use Python 3.11 runtime with a shared dependency layer (`sp-ingest-shared-deps`).
The query API Lambdas are conditional on `enable_webui = true`.

---

## DynamoDB Tables

| Table | Hash Key | Purpose | Notes |
|-------|----------|---------|-------|
| `sp-ingest-delta-tokens` | `drive_id` (S) | Stores Graph API delta link per SharePoint drive for incremental sync | PITR enabled |
| `sp-ingest-document-registry` | `s3_source_key` (S) | Tracks every document through `ingested -> extracting -> twin_ready` lifecycle | GSIs: `textract_status-index`, `sp_library-index`; PITR enabled |
| `doc-permission-mappings` | `s3_prefix` (S) | Maps S3 prefixes to allowed Entra ID groups + sensitivity level | GSI: `sensitivity_level-index`; PITR enabled |
| `user-group-cache` | `user_id` (S) | Caches SCIM-provisioned user-to-group memberships | TTL on `ttl_expiry` (24h); PITR enabled |

All tables use PAY_PER_REQUEST billing.

---

## S3 Bucket Structure

```
dynamo-ai-documents/
|-- source/{Org}/{Department}/        # Original documents from SharePoint
|-- twins/{Org}/{Department}/         # JSON text twins for Knowledge Base indexing
|-- quarantine/                       # Documents with unmapped permission prefixes
|-- governance-reports/               # Drift reports, compliance reports
```

Documents maintain a parallel key structure between `source/` and `twins/` so that
permission mappings based on S3 prefix apply consistently to both originals and twins.

---

## EventBridge Schedules

| Schedule | Lambda | Purpose |
|----------|--------|---------|
| `cron(0 7 * * ? *)` (daily 07:00 UTC) | `daily-sync` | SharePoint delta sync |
| `rate(15 minutes)` | `group-cache-refresh` | SCIM user/group sync from IAM Identity Center |
| `cron(0 2 ? * SUN *)` (Sunday 02:00 UTC) | `permission-drift-detector` | Check for unmapped S3 prefixes |
| `cron(0 3 * * ? *)` (daily 03:00 UTC) | `stale-account-cleanup` | Clean up deleted/disabled users |
| `cron(0 6 1 * ? *)` (1st of month 06:00 UTC) | `compliance-report` | Monthly compliance report |

---

## Environment Variables

Common variables configured across Lambda functions:

| Variable | Description |
|----------|-------------|
| `S3_BUCKET` | Document storage bucket name (`dynamo-ai-documents`) |
| `AWS_REGION_NAME` | AWS region (default: `us-east-1`) |
| `PYTHONPATH` | `/var/task/src:/opt/python` (Lambda layer path) |
| `LOG_LEVEL` | Logging level (`INFO`) |
| `DYNAMODB_DELTA_TABLE` | Delta tokens table name |
| `DYNAMODB_REGISTRY_TABLE` | Document registry table name |
| `PERMISSION_MAPPINGS_TABLE` | Permission mappings table name |
| `USER_GROUP_CACHE_TABLE` | User group cache table name |
| `IDENTITY_STORE_ID` | IAM Identity Center Identity Store ID |
| `GOVERNANCE_ALERTS_TOPIC_ARN` | SNS topic ARN for governance alerts |
| `TEXTRACT_SNS_TOPIC_ARN` | SNS topic ARN for Textract job notifications |
| `QUARANTINE_SNS_TOPIC_ARN` | SNS topic ARN for quarantine alerts |
| `KNOWLEDGE_BASE_ID` | Bedrock Knowledge Base ID (query handler) |
| `BEDROCK_MODEL_ID` | Default Bedrock model ID (query handler) |
| `GUARDRAIL_ID` / `GUARDRAIL_VERSION` | Bedrock Guardrail configuration (query handler) |
| `API_KEYS` | Comma-separated API keys (authorizer) |
| `API_KEY_USER_MAP` | JSON mapping of API key to user identity (authorizer) |
| `SECRET_PREFIX` | Secrets Manager prefix for Azure credentials (`sp-ingest/`) |
| `SHAREPOINT_SITE_NAME` | SharePoint site to crawl |
| `EXCLUDED_FOLDERS` | Comma-separated folder names to skip during sync |

---

## Monitoring and Alerting

### CloudWatch Dashboard

**`SP-Ingest-Pipeline`** -- 7 rows covering:

| Row | Widgets |
|-----|---------|
| 1 | Documents synced today, Textract jobs today, Textract failures, S3 object count |
| 2 | Lambda invocations, Lambda errors, Lambda duration (ingestion Lambdas) |
| 3 | DynamoDB consumed capacity, DynamoDB throttled requests |
| 4 | Document sync activity, extraction activity (custom metrics) |
| 5 | Query volume, permission-scoped null results, query latency, guardrail activations |
| 6 | SCIM sync health, governance Lambda invocations, quarantine document count |
| 7 | Governance Lambda errors, DLQ message depth |

### CloudWatch Alarms (13 total)

| Alarm | Severity | Condition |
|-------|----------|-----------|
| `daily-sync-errors` | CRITICAL | Daily sync Lambda errors > 0 |
| `daily-sync-missing` | CRITICAL | No daily sync invocation in 26 hours |
| `scim-sync-stale` | CRITICAL | No group-cache-refresh invocation in 2 hours |
| `quarantine-detected` | CRITICAL | Message published to quarantine SNS topic |
| `textract-complete-errors` | HIGH | Textract complete errors > 3 in 1 hour |
| `permission-null-rate` | HIGH | >20% of queries returning no results |
| `dlq-group-cache-refresh` | HIGH | Messages in group-cache-refresh DLQ |
| `dlq-permission-drift` | HIGH | Messages in permission-drift-detector DLQ |
| `dlq-stale-account` | HIGH | Messages in stale-account-cleanup DLQ |
| `dynamo-throttle-delta-tokens` | MEDIUM | DynamoDB throttled requests on delta tokens table |
| `dynamo-throttle-registry` | MEDIUM | DynamoDB throttled requests on registry table |
| `query-latency-p99` | MEDIUM | Query p99 latency > 10 seconds |
| `guardrail-triggered` | LOW | Bedrock guardrail intervened on a query |

### SNS Topics

| Topic | Purpose |
|-------|---------|
| `sp-ingest-alerts` | Primary alert channel (all CloudWatch alarms route here) |
| `sp-ingest-governance-alerts` | Governance-specific alerts (drift reports, compliance) |
| `doc-quarantine-alerts` | Quarantined document notifications |
| `sp-ingest-textract-notifications` | Textract job completion events (internal) |

---

## Query API

Deployed conditionally when `enable_webui = true`. Provides permission-filtered RAG queries
backed by Amazon Bedrock Knowledge Base.

**Endpoints (API Gateway HTTP API):**

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/query` | Submit a RAG query; results filtered by caller's group memberships |
| `GET` | `/health` | Health check |
| `GET` | `/user/permissions` | Return the authenticated user's group memberships and accessible prefixes |

**Features:**
- Lambda authorizer validates JWT tokens or API keys
- Bedrock Guardrails filter PII, sensitive content, and off-topic queries
- LLM Router selects Haiku, Sonnet, or Opus based on query complexity
- Optional Open WebUI frontend on ECS Fargate behind ALB

---

## Quick Start

### Prerequisites

- Python 3.11+
- AWS account with access to: S3, DynamoDB, Lambda, EventBridge, SNS, Textract, Bedrock,
  IAM Identity Center, Secrets Manager
- Azure AD app registration with `Sites.Read.All` Microsoft Graph API permission
- Terraform >= 1.5
- (Optional) Docker for bulk ingestion EC2 image

### Deploy

```bash
# 1. Clone the repository
git clone <repo-url> && cd dynamo-sharepoint-ingest

# 2. Configure Terraform variables
cd terraform
cp terraform.tfvars.example terraform.tfvars   # edit with your values

# 3. Build Lambda artifacts
cd .. && ./scripts/deploy.sh

# 4. Apply infrastructure
cd terraform && terraform init && terraform plan && terraform apply
```

Store Azure AD credentials in AWS Secrets Manager under the `sp-ingest/` prefix:
- `sp-ingest/azure-client-id`
- `sp-ingest/azure-client-secret`
- `sp-ingest/azure-tenant-id`

For a detailed step-by-step walkthrough, see [ingestion-runbook.md](ingestion-runbook.md).

---

## Development

### Local Setup

```bash
cd dynamo-sharepoint-ingest

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install all dependencies (including dev)
pip install -e ".[dev]"
# or
pip install -r requirements.txt
```

### Run Tests

```bash
# Unit tests (excludes integration tests)
python -m pytest tests/ --ignore=tests/integration -v

# With script
./scripts/test-local.sh
```

### Validate Terraform

```bash
cd terraform && terraform validate
```

### Code Style

```bash
ruff check src/ tests/
ruff format src/ tests/
```

### Project Structure

```
dynamo-sharepoint-ingest/
|-- src/                        # Lambda handler source code
|   |-- daily_sync.py           # Daily SharePoint sync handler
|   |-- textract_trigger.py     # S3-triggered extraction router
|   |-- textract_complete.py    # Textract job completion handler
|   |-- group_cache_refresh.py  # SCIM user/group sync
|   |-- permission_drift_detector.py
|   |-- stale_account_cleanup.py
|   |-- compliance_report_generator.py
|   |-- query_handler.py        # RAG query handler
|   |-- api_authorizer.py       # JWT/API key authorizer
|   |-- graph_client.py         # Microsoft Graph API client
|   |-- s3_client.py            # S3 operations
|   |-- textract_client.py      # Textract operations
|   |-- delta_tracker.py        # DynamoDB delta token persistence
|   |-- document_registry.py    # Document lifecycle tracking
|   |-- digital_twin.py         # JSON twin assembly
|   |-- bulk_ingest.py          # One-time bulk load (EC2)
|   +-- utils/                  # Shared utilities
|-- terraform/                  # Infrastructure as code
|-- tests/                      # Unit and integration tests
|-- layer/                      # Lambda layer dependencies
|-- dist/                       # Build artifacts (lambda-code.zip, lambda-layer.zip)
|-- docker/                     # Dockerfiles for bulk ingestion
|-- scripts/                    # Build, deploy, and test scripts
|-- config/                     # Configuration files
|-- docs/                       # Documentation
|   |-- runbooks/               # Operational runbooks
|   +-- plans/                  # Design documents
+-- entra-id/                   # Entra ID / SCIM configuration
```

---

## Operational Runbooks

| Runbook | Description |
|---------|-------------|
| [ADD_NEW_DEPARTMENT.md](runbooks/ADD_NEW_DEPARTMENT.md) | Add a new department to the ingestion pipeline and permission mappings |
| [ONBOARD_NEW_USER.md](runbooks/ONBOARD_NEW_USER.md) | Grant a new user access through Entra ID group assignment |
| OFFBOARD_USER.md | Revoke user access and clean up cached permissions |
| ADD_NEW_DOCUMENT_LIBRARY.md | Configure a new SharePoint document library for sync |
| [HANDLE_QUARANTINED_DOCUMENT.md](runbooks/HANDLE_QUARANTINED_DOCUMENT.md) | Investigate and resolve quarantined documents |
| INVESTIGATE_PERMISSION_DENIAL.md | Debug why a user cannot access expected documents |
| EMERGENCY_REVOKE_ACCESS.md | Immediately revoke a user's access across the pipeline |

---

## Additional Documentation

| Document | Description |
|----------|-------------|
| [EXISTING_ARCHITECTURE.md](EXISTING_ARCHITECTURE.md) | Detailed existing architecture reference |
| [ENTRA_ID_SCIM_SETUP_RUNBOOK.md](ENTRA_ID_SCIM_SETUP_RUNBOOK.md) | Step-by-step SCIM provisioning setup guide |
| [PERMISSION_MAPPING_VALIDATION.md](PERMISSION_MAPPING_VALIDATION.md) | Validation procedures for permission mappings |
| [ingestion-runbook.md](ingestion-runbook.md) | End-to-end ingestion pipeline runbook |
| [lessons-learned.md](lessons-learned.md) | Deployment lessons learned |
