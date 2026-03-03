# Existing Architecture — SharePoint-to-S3 Document Ingestion Pipeline

> Generated: 2026-03-03 | Codebase audit of `dynamo-sharepoint-ingest`

---

## Pipeline Flow Diagram

```
                           +--------------------------+
                           |   SharePoint Online      |
                           |   (Dynamo Intranet site) |
                           +------------+-------------+
                                        |
                        Microsoft Graph API (MSAL client-credentials)
                                        |
            +---------------------------+---------------------------+
            |                                                       |
  +---------v----------+                                 +----------v-----------+
  | bulk_ingest.py     |                                 | daily_sync.py        |
  | (EC2 - one-time)   |                                 | (Lambda - daily)     |
  | Full recursive     |                                 | EventBridge cron     |
  | crawl, 5 threads   |                                 | 7 AM UTC             |
  +--------+-----------+                                 | iter_delta() stream  |
           |                                             +----------+-----------+
           |                                                        |
           +------------------+-------------------------------------+
                              |
                              v
              +---------------+----------------+
              |  S3: dynamo-ai-documents       |
              |  Prefix: source/{site}/{lib}/  |
              |  (AES256 encrypted, tagged)    |
              +---------------+----------------+
                              |
                   S3 PutObject event notification
                   (.pdf, .docx, .pptx, .xlsx, .txt)
                              |
                              v
              +---------------+----------------+
              |  textract_trigger.py (Lambda)  |
              |  Routes by file type:          |
              |  PDF/DOCX -> Textract async    |
              |  PPTX/XLSX -> python-pptx/     |
              |               openpyxl direct  |
              |  TXT -> UTF-8 read             |
              +--+------------------+----------+
                 |                  |
     Textract    |                  | Direct extract
     async job   |                  | (twin built immediately)
                 v                  |
      +----------+--------+        |
      | AWS Textract       |        |
      | (TABLES + FORMS)   |        |
      +----------+---------+        |
                 |                  |
      SNS notification              |
      (job complete)                |
                 |                  |
                 v                  |
      +----------+------------+    |
      | textract_complete.py  |    |
      | (Lambda)              |    |
      | Get results, build    |    |
      | JSON twin             |    |
      +----------+------------+    |
                 |                  |
                 +--------+---------+
                          |
                          v
              +-----------+------------+
              | S3: dynamo-ai-documents|
              | Prefix: extracted/     |
              | {site}/{lib}/*.json    |
              | (JSON digital twins)   |
              +-----------+------------+
                          |
                          v
              +-----------+------------+
              | chunker.py (offline)   |
              | 512-token chunks with  |
              | 50-token overlap       |
              | Output: JSONL          |
              +------------------------+
                          |
                          v
              +------------------------+
              | [NOT YET IMPLEMENTED]  |
              | Vector store /         |
              | embedding pipeline     |
              +------------------------+

  State tracking:
  +----------------------------------+    +----------------------------------+
  | DynamoDB: sp-ingest-delta-tokens |    | DynamoDB: sp-ingest-document-    |
  | PK: drive_id                     |    | registry                         |
  | Stores: delta_token, sync_count  |    | PK: s3_source_key                |
  | Read/Write: daily_sync,          |    | GSIs: textract_status-index,     |
  |             bulk_ingest          |    |       sp_library-index           |
  +----------------------------------+    | Read/Write: all Lambdas +        |
                                          |             bulk_ingest          |
                                          +----------------------------------+
```

---

## Lambda Functions

| Function Name | Handler | Trigger | Timeout | Memory | Purpose |
|---|---|---|---|---|---|
| `sp-ingest-daily-sync` | `src.daily_sync.handler` | EventBridge cron `0 7 * * ? *` (7 AM UTC daily), 2 retries | 900s | 512 MB | Incremental delta sync from SharePoint via Graph API. Downloads new/modified files to S3, removes deleted files, updates registry. |
| `sp-ingest-textract-trigger` | `src.textract_trigger.handler` | S3 `ObjectCreated` on `source/` prefix for `.pdf`, `.docx`, `.pptx`, `.xlsx`, `.txt` | 300s | 1024 MB | Routes uploaded documents to extraction: PDF/DOCX -> async Textract, PPTX/XLSX -> python-pptx/openpyxl direct extract, TXT -> UTF-8 read. Builds twins for non-PDF types immediately. |
| `sp-ingest-textract-complete` | `src.textract_complete.handler` | SNS topic `sp-ingest-textract-notifications` (Textract job completion) | 300s | 1024 MB | Retrieves Textract results, builds JSON digital twin, uploads to S3 `extracted/`, updates registry to `completed` or `failed`. |

### Lambda Environment Variables

**daily-sync:**
| Variable | Value |
|---|---|
| `PYTHONPATH` | `/var/task/src:/opt/python` |
| `SHAREPOINT_SITE_NAME` | `Dynamo` |
| `EXCLUDED_FOLDERS` | `Drafts,drafts` |
| `S3_BUCKET` | `dynamo-ai-documents` |
| `S3_SOURCE_PREFIX` | `source` |
| `S3_EXTRACTED_PREFIX` | `extracted` |
| `DYNAMODB_DELTA_TABLE` | `sp-ingest-delta-tokens` |
| `DYNAMODB_REGISTRY_TABLE` | `sp-ingest-document-registry` |
| `SECRET_PREFIX` | `sp-ingest/` |
| `AWS_REGION_NAME` | `us-east-1` |
| `LOG_LEVEL` | `INFO` |

**textract-trigger:** `PYTHONPATH`, `S3_BUCKET`, `DYNAMODB_REGISTRY_TABLE`, `TEXTRACT_SNS_TOPIC_ARN`, `TEXTRACT_SNS_ROLE_ARN`, `LOG_LEVEL`

**textract-complete:** `PYTHONPATH`, `S3_BUCKET`, `DYNAMODB_REGISTRY_TABLE`, `LOG_LEVEL`

### Lambda IAM Permissions

| Role | S3 | DynamoDB | Textract | Secrets Manager | Other |
|---|---|---|---|---|---|
| `sp-ingest-daily-sync-lambda-role` | get/put/delete/list/tag on `dynamo-ai-documents` | Full CRUD on both tables + GSIs | - | `GetSecretValue` on 3 secrets | CloudWatch Logs |
| `sp-ingest-textract-trigger-lambda-role` | get/put/list/tag | put/get/update on registry | `StartDocumentAnalysis`, `StartDocumentTextDetection` | - | SNS publish, IAM PassRole |
| `sp-ingest-textract-complete-lambda-role` | get/put/tag | get/update on registry | `GetDocumentAnalysis`, `GetDocumentTextDetection` | - | CloudWatch Logs |

### Shared Lambda Layer

- **Name:** `sp-ingest-shared-deps`
- **Runtime:** Python 3.11
- **Packages:** msal, requests, python-pptx, openpyxl, python-docx, pyyaml, python-dotenv

---

## DynamoDB Tables

### Table 1: `sp-ingest-delta-tokens`

| Attribute | Type | Role |
|---|---|---|
| `drive_id` | String | **Partition Key** |
| `delta_token` | String | Opaque Graph API delta token |
| `last_sync_at` | String (ISO 8601) | Timestamp of last successful sync |
| `items_processed` | Number | Items processed in last sync |
| `sync_count` | Number | Atomic counter of total syncs (ADD operation) |

- **Billing:** PAY_PER_REQUEST
- **PITR:** Enabled
- **GSIs:** None
- **TTL:** None
- **Read by:** `daily_sync.py`, `bulk_ingest.py`
- **Written by:** `daily_sync.py`, `bulk_ingest.py`

### Table 2: `sp-ingest-document-registry`

| Attribute | Type | Role |
|---|---|---|
| `s3_source_key` | String | **Partition Key** (e.g., `source/Dynamo/Dynamo-Documents/HR/doc.pdf`) |
| `sp_item_id` | String | SharePoint item ID |
| `sp_path` | String | SharePoint-relative path |
| `sp_library` | String | SharePoint library name |
| `sp_last_modified` | String (ISO 8601) | SharePoint last-modified timestamp |
| `file_type` | String | Extension (e.g., `.pdf`, `.docx`) |
| `size_bytes` | Number | File size |
| `textract_status` | String | `pending` -> `processing` -> `completed` or `failed` |
| `textract_job_id` | String | Textract async job ID (for PDFs) |
| `s3_twin_key` | String | S3 key of extracted JSON twin |
| `ingested_at` | String (ISO 8601) | First ingestion timestamp |
| `updated_at` | String (ISO 8601) | Last update timestamp |

- **Billing:** PAY_PER_REQUEST
- **PITR:** Enabled
- **TTL:** None

**GSI 1: `textract_status-index`**
| Key | Attribute | Type |
|---|---|---|
| Partition Key | `textract_status` | String |
| Sort Key | `ingested_at` | String |

Used to query pending/failed documents for retry workflows.

**GSI 2: `sp_library-index`**
| Key | Attribute | Type |
|---|---|---|
| Partition Key | `sp_library` | String |
| Sort Key | `sp_last_modified` | String |

Used to query documents by SharePoint library.

- **Read by:** `daily_sync.py`, `textract_trigger.py`, `textract_complete.py`, `bulk_ingest.py`
- **Written by:** `daily_sync.py`, `textract_trigger.py`, `textract_complete.py`, `bulk_ingest.py`

---

## S3 Bucket Structure

**Bucket:** `dynamo-ai-documents`
- **Encryption:** AES256 server-side
- **Public access:** Fully blocked
- **Versioning:** Disabled

### Prefix Structure

```
dynamo-ai-documents/
├── source/                          # Raw SharePoint documents
│   └── {site_name}/                 # e.g., "Dynamo"
│       └── {library_name}/          # e.g., "Dynamo-Documents", "HR-Files"
│           └── {folder_path}/       # Mirrors SharePoint folder hierarchy
│               └── {filename}       # Original filename (sanitized)
│
├── extracted/                       # JSON digital twins
│   └── {site_name}/
│       └── {library_name}/
│           └── {folder_path}/
│               └── {filename}.json  # Same path as source, .json extension
│
└── textract-raw/                    # Raw Textract output (written by Textract service)
    └── {textract_job_id}/
```

### S3 Event Notifications

| Event | Prefix | Suffixes | Target |
|---|---|---|---|
| `s3:ObjectCreated:*` | `source/` | `.pdf`, `.docx`, `.pptx`, `.xlsx`, `.txt` | `sp-ingest-textract-trigger` Lambda |

### S3 Object Tags (applied during upload)

Each document in `source/` is tagged with:

| Tag Key | Example Value | Source |
|---|---|---|
| `sp-site` | `Dynamo` | Config: `SHAREPOINT_SITE_NAME` |
| `sp-library` | `Dynamo Documents` | Graph API library name |
| `sp-path` | `/Accounting/Dynamo PTO Policy.pdf` | SharePoint relative path |
| `file-type` | `pdf` | File extension |
| `sp-last-modified` | `2026-01-08T19:22:27Z` | Graph API `lastModifiedDateTime` |
| `sp-author` | `Anh _Nicky_ Nguyen` | Graph API `createdBy.user.displayName` (when available) |
| `sp-content-type` | `application/pdf` | Graph API `file.mimeType` (when available) |
| `access-tags` | `hr,leadership,admin` | `AccessControlMapper` library-pattern match |

### S3 Bucket Policy

Textract service principal (`textract.amazonaws.com`) is allowed:
- `s3:GetObject` on `source/*`
- `s3:PutObject` on `textract-raw/*`

---

## SharePoint-to-S3 Mapping

### Site/Library/Folder to S3 Prefix

```
SharePoint site "Dynamo"
  └── Library "Dynamo Documents"
      └── Folder "Accounting/Additional Useful Documents"
          └── File "Dynamo PTO Policy.pdf"

Maps to S3 key:
  source/Dynamo/Dynamo-Documents/Accounting/Additional-Useful-Documents/Dynamo-PTO-Policy.pdf

Path sanitization rules (PathMapper):
  - Spaces -> hyphens
  - Special characters stripped (preserves Unicode)
  - Double slashes collapsed
  - S3 key limit enforced (1024 bytes)
```

### Delta Detection (Incremental Sync)

1. **Trigger:** EventBridge cron fires `sp-ingest-daily-sync` at 7 AM UTC daily.
2. **Token retrieval:** For each drive, `DeltaTracker.get_delta_token(drive_id)` reads from `sp-ingest-delta-tokens`. `None` = full initial delta.
3. **Graph API call:** `GraphClient.iter_delta(drive_id, token)` calls `GET /drives/{id}/root/delta?token={token}`. Streams results page-by-page.
4. **Change detection:** Each item in the delta response is categorized:
   - **Deleted:** `"deleted"` key present -> remove from S3 and registry.
   - **Unchanged:** `sp_last_modified` matches registry -> skip.
   - **New/Modified:** Download file, upload to S3 with tags, register/update in DynamoDB.
5. **Token save:** After processing all items for a drive, the new delta token is saved to DynamoDB for the next run.
6. **Retry:** EventBridge configured with 2 retries on failure.

### Permission/Access Metadata During Ingestion

The pipeline captures **library-level access control** via the `AccessControlMapper`:

- **Rules file:** `src/config/access_rules.yaml` maps SharePoint library name patterns to access tags using fnmatch.
- **Tag application:** During ingestion (both bulk and daily sync), `acl.map_document(lib_name, sp_path)` returns matching access tags.
- **Storage:** Tags are stored as the `access-tags` S3 object tag (comma-separated).
- **Role mappings:** `role_mappings` in the YAML define which user roles can access which tags.

**What is NOT captured:** SharePoint item-level permissions, Azure AD group memberships, individual user sharing, or site-level permissions. The current system uses a static YAML-based mapping at the library level only.

---

## Digital Twin Schema (v2.0)

Each extracted document produces a JSON twin in `extracted/`:

```json
{
  "schema_version": "2.0",
  "document_id": "<SHA-256 of s3_source_key>",
  "source_s3_key": "source/Dynamo/...",
  "source_sharepoint_url": "",
  "filename": "Document Name.pdf",
  "file_type": ".pdf",
  "content_type": "",
  "metadata": {
    "sp_library": "Dynamo Documents",
    "sp_path": "/Accounting/Document Name.pdf",
    "sp_item_id": "01S5LTWX...",
    "sp_last_modified": "2021-02-10T20:23:19Z",
    "size_bytes": "203584"
  },
  "extracted_text": "<full concatenated text>",
  "pages": [
    { "page_number": 1, "text": "..." },
    { "page_number": 2, "text": "..." }
  ],
  "tables": [
    { "table_index": 1, "rows": [["col1", "col2"], ["val1", "val2"]] }
  ],
  "extraction_metadata": {
    "method": "textract-document-analysis|direct-pptx|direct-xlsx|direct-docx|plain-text",
    "average_confidence": 99.2,
    "total_pages": 3,
    "extracted_at": "2026-02-12T..."
  }
}
```

---

## Chunker (Offline/Batch)

`src/chunker.py` provides `DocumentChunker` for breaking twins into vector-embedding-ready records:

- **Chunk size:** 512 tokens (default), 50-token overlap
- **Splitting strategy:** Hierarchical: paragraphs -> sentences -> words
- **Output format:** JSONL with fields: `chunk_id`, `document_id`, `source_s3_key`, `filename`, `chunk_index`, `total_chunks`, `text`, `metadata` (includes `sp_library`, `sp_path`, `file_type`, `pages` provenance)
- **Status:** Code exists but is not yet wired into an automated pipeline or vector store.

---

## OpenSearch / Vector Store Configuration

**No vector store is currently deployed.** There is no OpenSearch domain, no Bedrock Knowledge Base, no Pinecone/Weaviate/Qdrant/ChromaDB configuration, and no embedding model integration anywhere in the codebase or Terraform.

The `chunker.py` module is the bridge point — it produces JSONL output suitable for ingestion into a vector store, but the downstream pipeline does not exist yet.

---

## Infrastructure as Code (Terraform)

All infrastructure is managed via Terraform in `terraform/`:

| File | Resources |
|---|---|
| `main.tf` | AWS provider, S3 backend |
| `variables.tf` | All input variables with defaults |
| `lambda.tf` | 3 Lambda functions + shared layer + permissions |
| `s3.tf` | S3 bucket, encryption, public access block, bucket policy, event notifications |
| `dynamodb.tf` | 2 DynamoDB tables (PAY_PER_REQUEST, PITR) |
| `iam.tf` | 5 IAM roles with inline policies |
| `sns.tf` | Textract notification topic + Lambda subscription |
| `eventbridge.tf` | Daily cron rule + Lambda target |
| `monitoring.tf` | Log groups, metric filters, alarms, dashboard |
| `secrets.tf` | 3 Secrets Manager secrets (Azure AD credentials) |
| `ec2-bulk.tf` | Conditional EC2 bulk loader (VPC, SG, instance) |
| `outputs.tf` | 18 outputs (ARNs, URLs, conditional EC2 info) |

**State:** S3 backend at `s3://dynamo-terraform-state-760560299079/sharepoint-ingest/terraform.tfstate`

---

## CloudWatch Monitoring

### Log Groups (30-day retention)

| Log Group | Source | Notes |
|---|---|---|
| `/aws/lambda/sp-ingest-daily-sync` | Daily sync Lambda | **Active** (default Lambda log group) |
| `/aws/lambda/sp-ingest-textract-trigger` | Textract trigger Lambda | **Active** |
| `/aws/lambda/sp-ingest-textract-complete` | Textract complete Lambda | **Active** |
| `/sp-ingest/daily-sync` | (unused) | Custom group created by Terraform but Lambdas log to default groups |
| `/sp-ingest/textract-trigger` | (unused) | Same issue |
| `/sp-ingest/textract-complete` | (unused) | Same issue |
| `/sp-ingest/bulk-ingest` | EC2 bulk loader | Used during bulk load only |

### Alarms

| Alarm | Condition | Status |
|---|---|---|
| `sp-ingest-daily-sync-errors` | Any Lambda error in 5-min window | OK |
| `sp-ingest-textract-complete-errors` | >3 Lambda errors in 1 hour | OK |
| `sp-ingest-daily-sync-missing` | <1 invocation in 26 hours (treat_missing_data: breaching) | OK |
| `sp-ingest-dynamo-throttle-delta-tokens` | Any throttled DynamoDB request | OK |
| `sp-ingest-dynamo-throttle-registry` | Any throttled DynamoDB request | OK |

### Dashboard

**Name:** `SP-Ingest-Pipeline` — 4 rows: ingestion counters, Lambda metrics, DynamoDB capacity, sync/extraction activity charts.

---

## Current Stats (as of 2026-03-03)

| Metric | Value |
|---|---|
| S3 source objects | 4,334 |
| S3 extracted twins | ~2,662 (from initial bulk load + some from re-sync) |
| Registry total | 4,334 |
| Registry completed | ~2,682+ |
| Registry failed | ~17 (mostly `.doc` legacy format) |
| Delta tokens | 26 drives tracked |
| SharePoint libraries | 26 |

---

## GAPS TO ADDRESS

### 1. No Vector Store / Embedding Pipeline
- `chunker.py` exists but is not wired into any automated pipeline.
- No OpenSearch domain, Bedrock Knowledge Base, or other vector store is deployed.
- No embedding model is configured or invoked.
- Chunks are not being generated or indexed automatically.

### 2. Permission Metadata is Library-Level Only (Static YAML)
- Access control is based on static `fnmatch` patterns against **library names** in `access_rules.yaml`.
- **SharePoint item-level permissions are not captured.** The Graph API provides per-item permission endpoints (`/drives/{id}/items/{id}/permissions`) but these are never called.
- **Azure AD group memberships are not resolved.** The rules map to abstract tags (e.g., `hr`, `leadership`) but there is no integration with Azure AD to resolve which users belong to which groups.
- **No user-level sharing information is captured.** Documents shared with specific users in SharePoint are not reflected in the access tags.
- Access tags are stored only as S3 object tags — they are **not propagated into the digital twin JSON** or the chunker output metadata.

### 3. Access Tags Not in Digital Twins or Chunks
- The `access-tags` S3 object tag is applied during ingestion, but `digital_twin.py` does not read or include access tags in the twin JSON.
- `chunker.py` copies metadata from the twin, so chunks also lack access tags.
- For a RAG pipeline with access control, every chunk in the vector store needs access metadata for query-time filtering.

### 4. Extracted Twins May Be Stale for Re-Synced Documents
- The daily sync re-uploads changed documents to S3 `source/`, which triggers `textract_trigger` via S3 event.
- However, 1,632 documents were recently re-synced and many may not yet have updated twins in `extracted/`.
- The S3 event notification should trigger extraction automatically, but the pipeline may have a backlog.

### 5. CloudWatch Metric Filters Watch Wrong Log Groups
- Terraform creates custom log groups (`/sp-ingest/*`) with metric filters, but Lambdas log to the default `/aws/lambda/*` groups.
- Custom metrics (`DocumentsSynced`, `TextractCompleted`, etc.) are never populated.
- The dashboard widgets relying on these custom metrics show no data.

### 6. No Automated Reconciliation
- `scripts/reconcile.sh` exists for manual reconciliation between S3 `source/` and `extracted/`.
- There is no automated process to detect and fix missing twins or stale extractions.

### 7. Legacy `.doc` Files Not Supported
- 17 documents with `.doc` extension failed extraction. The pipeline supports `.docx` (via python-docx or Textract) but not legacy `.doc` (binary Word format).
- These would need LibreOffice conversion (available on EC2 path, not Lambda).

### 8. `source_sharepoint_url` Always Empty in Twins
- The `digital_twin.py` schema includes `source_sharepoint_url` but it is always set to `""`.
- The Graph API `webUrl` field is available during crawl but not propagated through to the twin builder.

### 9. Lambda Memory for Full Re-Sync
- The daily-sync Lambda at 512 MB works for incremental deltas but cannot handle full initial deltas on large drives (requires 9+ GB).
- If delta tokens are ever lost, the Lambda cannot recover without temporary memory increase.
