# Dynamo SharePoint Ingest

A governed enterprise AI platform that syncs SharePoint documents into AWS, extracts and indexes content for RAG queries via Amazon Bedrock, and enforces data governance natively — every document permission-scoped, every query audited, every response guardrailed. Built so that adding a new department's documents is a 3-step process with automatic protection from day one.

## Architecture

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
    | daily-sync      |                        | EC2 Bulk Loader  |
    | (EventBridge    |                        | (initial load)   |
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
                   |  textract-trigger          |
                   |  (routes by file type;     |
                   |   quarantines unmapped)    |
                   +-----+------------+--------+
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
                 +---------------------------+        +------------------+
                 | query-handler             |<-------| API Gateway      |
                 | (POST /query,             |        | + Lambda         |
                 |  GET /health,             |        |   Authorizer     |
                 |  GET /user/permissions)   |        +--------+---------+
                 +---------------------------+                 |
                              |                                v
                              v                     +------------------+
                 +---------------------------+      | Open WebUI       |
                 | Bedrock Guardrails        |      | (ECS Fargate,    |
                 | (PII / content / topic)   |      |  optional)       |
                 | + LLM Router              |      +------------------+
                 | (Haiku / Sonnet / Opus)   |
                 +---------------------------+

    GOVERNANCE (runs on schedule, zero manual intervention):
    +---------------------+  +-------------------------+  +---------------------+
    | group-cache-refresh |  | permission-drift-       |  | stale-account-      |
    | (every 15 min)      |  | detector (Sun 02:00)    |  | cleanup (daily)     |
    +---------------------+  +-------------------------+  +---------------------+
    +---------------------+
    | compliance-report   |
    | (1st of month)      |
    +---------------------+
```

## How It Works

1. **Sync** — `daily-sync` calls Microsoft Graph API delta queries to discover new, changed, and deleted documents in SharePoint. Documents land in `source/` in S3. Delta tokens are persisted in DynamoDB for incremental syncs.

2. **Extract** — S3 PutObject events trigger `textract-trigger`. PDFs go to Amazon Textract (async). DOCX, PPTX, XLSX, and TXT files are extracted directly in-Lambda. Documents uploaded to S3 prefixes without permission mappings are quarantined — never exposed to the LLM.

3. **Twin** — On extraction completion, a JSON text twin is written to `twins/` with the document's full metadata: source path, SharePoint item ID, content hash, timestamps, and the permission groups inherited from the S3 prefix.

4. **Index** — Amazon Bedrock Knowledge Base indexes the text twins. Each chunk inherits `allowed_groups` and `sensitivity_level` metadata from the twin, making permission filtering possible at retrieval time.

5. **Query** — Users query via API Gateway. The query handler looks up the caller's group memberships and sensitivity ceiling, filters Bedrock KB results to only matching documents, applies Bedrock Guardrails, and routes to the appropriate LLM (Haiku/Sonnet/Opus) based on query complexity.

6. **Govern** — Four scheduled Lambdas run continuously with zero manual intervention: SCIM sync keeps group memberships fresh (every 15 min), drift detection catches unmapped prefixes (weekly), stale account cleanup purges disabled users (daily), and compliance reporting aggregates access patterns (monthly).

## Native Data Governance

Governance in this system is not a bolt-on layer — it is woven into every stage of the pipeline. Seven mechanisms run automatically with zero manual intervention:

| Mechanism | How It Works | Cadence |
|-----------|-------------|---------|
| **SCIM Sync** | Entra ID groups replicated to AWS via IAM Identity Center; `group-cache-refresh` flattens nested groups into DynamoDB | Every 15 min |
| **Permission-Scoped Queries** | Every RAG query filtered by the caller's group memberships + sensitivity ceiling before results are returned | Every query |
| **Automatic Quarantine** | Documents uploaded to S3 prefixes without permission mappings are quarantined — never exposed to the LLM | On ingestion |
| **Drift Detection** | Weekly scan compares S3 prefixes against permission mappings; alerts on unmapped prefixes, stale mappings, orphaned groups | Weekly |
| **Stale Account Cleanup** | Deleted/disabled users in Entra ID automatically have cached permissions purged with 90-day TTL | Daily |
| **Compliance Reporting** | Monthly report aggregates query volume, permission denials, quarantine status, SCIM uptime, group changes, drift | Monthly |
| **Audit Logging** | Every query logged with user identity, groups, filters, retrieved documents, latency — but never the raw query text (SHA-256 hash only) | Every query |

The result: a document uploaded to SharePoint is automatically synced, extracted, permission-tagged, indexed, and query-filtered without anyone configuring access rules per document. Permissions follow the folder structure.

## Bedrock Guardrails: Defense-in-Depth for a Company-Wide LLM

Amazon Bedrock Guardrails sit between the Knowledge Base retrieval and the LLM response, providing a second layer of protection beyond permission filtering:

- **PII Protection** — SSN, credit card numbers, phone numbers, email addresses, and tax IDs are automatically anonymized in model responses before they reach the user.

- **Topic Blocking** — The model refuses to generate personal medical advice, legal advice, or investment advice — even if the source documents contain relevant information. The guardrail intervenes regardless of how the question is phrased.

- **Content Filtering** — Hate speech, violence, sexual content, and misconduct are filtered at HIGH threshold on both input prompts and model output.

- **Company-Wide Coverage** — These guardrails apply to ALL queries across ALL data sources. When the Bedrock Knowledge Base is expanded to include Finance, HR, Legal, or any other department's documents, the guardrails automatically protect every response. No per-department guardrail configuration needed — one guardrail covers the entire LLM.

This is the foundation for a company-wide AI assistant. Permission filtering controls *who can see what*. Guardrails control *what the model is allowed to say*. Together, they make it safe to point any employee at a single query endpoint backed by the entire organization's document corpus.

## Automatic Protection for New Data Sources

Adding a new department's documents to the platform is a 3-step process:

1. **Create S3 prefix** (`source/Dynamo/Finance/`) — documents sync here from SharePoint
2. **Add permission mapping** — one DynamoDB entry mapping the prefix to an Entra ID group + sensitivity level
3. **That's it** — everything else is automatic:
   - Drift detector confirms the mapping exists (weekly)
   - Documents without mappings are quarantined (never exposed)
   - SCIM sync keeps user-to-group memberships fresh (every 15 min)
   - Bedrock Guardrails apply to all queries regardless of data source
   - Compliance reports track access patterns across all departments

**Example configuration with sensitivity levels:**

```
source/Dynamo/Finance/     → Finance group,     sensitivity: confidential
source/Dynamo/HR/          → HR group,          sensitivity: restricted
source/Dynamo/Engineering/ → Engineering group,  sensitivity: internal
source/Dynamo/Marketing/   → All-staff group,    sensitivity: public
```

A user in the Engineering group can query Engineering (`internal`) and Marketing (`public`) documents but cannot see Finance (`confidential`) or HR (`restricted`) — even if they ask the LLM directly. The permission filter removes those chunks before the model ever sees them.

## Permission Model Deep Dive

Access control operates on two axes, enforced simultaneously at the Bedrock KB retrieval layer:

**Group membership (horizontal axis):** Entra ID groups are synced to AWS via SCIM and cached in DynamoDB. Each document twin carries `allowed_groups` metadata inherited from its S3 prefix. At query time, only chunks whose `allowed_groups` intersect with the caller's groups are returned.

**Sensitivity ceiling (vertical axis):** Each S3 prefix has a sensitivity level — `public` (0), `internal` (1), `confidential` (2), `restricted` (3). Each user has a maximum sensitivity ceiling. Chunks above the caller's ceiling are filtered out, even if group membership matches.

Both axes are evaluated before any document chunk reaches the LLM. A user must satisfy *both* group membership *and* sensitivity level to see a given document.

## Monitoring & Alerting

The platform includes 13 CloudWatch alarms, a 7-row operational dashboard, and 4 SNS topics covering the full pipeline:

- **CRITICAL**: Daily sync errors/missing, SCIM sync stale (>2 hours), quarantine detected
- **HIGH**: Textract errors, permission null rate >20%, DLQ messages on governance Lambdas
- **MEDIUM**: DynamoDB throttling, query p99 latency >10s
- **LOW**: Guardrail activations

Dashboard rows cover document sync activity, extraction, DynamoDB capacity, query volume/latency, SCIM health, governance Lambda status, and DLQ depth.

For full alarm definitions, SNS topic details, and dashboard widget layout, see [docs/README.md](docs/README.md#monitoring-and-alerting).

## Components

### Lambda Functions (9)

| Lambda | Trigger | Purpose |
|--------|---------|---------|
| `daily-sync` | EventBridge cron (daily) | Incremental SharePoint sync via Graph API delta queries |
| `textract-trigger` | S3 PutObject on `source/` | Routes documents to extraction; quarantines unmapped prefixes |
| `textract-complete` | SNS (Textract done) | Retrieves Textract results, writes JSON text twin |
| `group-cache-refresh` | EventBridge rate (15 min) | SCIM sync — caches Entra ID user/group memberships |
| `permission-drift-detector` | EventBridge cron (weekly) | Scans S3 for unmapped prefixes, publishes drift report |
| `stale-account-cleanup` | EventBridge cron (daily) | Removes deleted/disabled users from group cache |
| `compliance-report` | EventBridge cron (monthly) | Generates compliance report to `governance-reports/` |
| `query-handler` | API Gateway | Permission-filtered RAG queries via Bedrock KB |
| `api-authorizer` | API Gateway | JWT and API key validation |

### DynamoDB Tables (4)

| Table | Purpose |
|-------|---------|
| `sp-ingest-delta-tokens` | Graph API delta link per SharePoint drive |
| `sp-ingest-document-registry` | Document lifecycle tracking (`ingested → extracting → twin_ready`) |
| `doc-permission-mappings` | S3 prefix → Entra ID group + sensitivity level |
| `user-group-cache` | Cached SCIM user-to-group memberships (24h TTL) |

For full table schemas, GSIs, Lambda timeouts, and memory configurations, see [docs/README.md](docs/README.md).

## Getting Started

### Prerequisites

- Python 3.11+
- AWS account with access to: S3, DynamoDB, Lambda, EventBridge, SNS, Textract, Bedrock, IAM Identity Center, Secrets Manager
- Azure AD app registration with `Sites.Read.All` Microsoft Graph API permission
- Terraform >= 1.5

### Quick Deploy

```bash
# 1. Clone the repository
git clone <repo-url> && cd dynamo-sharepoint-ingest

# 2. Configure Terraform variables
cd terraform && cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your values

# 3. Build and deploy
cd .. && ./scripts/deploy.sh
cd terraform && terraform init && terraform plan && terraform apply
```

Store Azure AD credentials in AWS Secrets Manager under the `sp-ingest/` prefix:
- `sp-ingest/azure-client-id`
- `sp-ingest/azure-client-secret`
- `sp-ingest/azure-tenant-id`

For the full step-by-step deployment walkthrough, see [docs/DEPLOYMENT_CHECKLIST.md](docs/DEPLOYMENT_CHECKLIST.md).

## Operational Runbooks

| Runbook | Description |
|---------|-------------|
| [Add New Department](docs/runbooks/ADD_NEW_DEPARTMENT.md) | Add a new department to the pipeline and permission mappings |
| [Onboard New User](docs/runbooks/ONBOARD_NEW_USER.md) | Grant access through Entra ID group assignment |
| [Offboard User](docs/runbooks/OFFBOARD_USER.md) | Revoke access and clean up cached permissions |
| [Add Document Library](docs/runbooks/ADD_NEW_DOCUMENT_LIBRARY.md) | Configure a new SharePoint document library for sync |
| [Handle Quarantined Document](docs/runbooks/HANDLE_QUARANTINED_DOCUMENT.md) | Investigate and resolve quarantined documents |
| [Investigate Permission Denial](docs/runbooks/INVESTIGATE_PERMISSION_DENIAL.md) | Debug why a user cannot access expected documents |
| [Emergency Revoke Access](docs/runbooks/EMERGENCY_REVOKE_ACCESS.md) | Immediately revoke a user's access across the pipeline |

## Documentation

| Document | Description |
|----------|-------------|
| [docs/README.md](docs/README.md) | Full technical reference — architecture, tables, alarms, query API |
| [docs/DEPLOYMENT_CHECKLIST.md](docs/DEPLOYMENT_CHECKLIST.md) | Ordered deployment steps from scratch to production |
| [docs/EXISTING_ARCHITECTURE.md](docs/EXISTING_ARCHITECTURE.md) | Detailed existing architecture reference |
| [docs/ENTRA_ID_SCIM_SETUP_RUNBOOK.md](docs/ENTRA_ID_SCIM_SETUP_RUNBOOK.md) | Step-by-step SCIM provisioning setup guide |
| [docs/PERMISSION_MAPPING_VALIDATION.md](docs/PERMISSION_MAPPING_VALIDATION.md) | Validation procedures for permission mappings |
| [docs/ingestion-runbook.md](docs/ingestion-runbook.md) | End-to-end ingestion pipeline runbook |
| [docs/lessons-learned.md](docs/lessons-learned.md) | Deployment lessons learned |
