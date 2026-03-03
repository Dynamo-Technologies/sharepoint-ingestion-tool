# Add New Document Library Runbook

Add a new SharePoint document library to the ingestion pipeline. This sets up
the S3 prefix, configures DynamoDB permission mappings, and verifies documents
flow through the pipeline correctly.

## Prerequisites

- AWS CLI configured with permissions for S3, DynamoDB, and Lambda
- The SharePoint document library name and its site
- Knowledge of which Entra ID security group(s) should have access to this library
- The group Object IDs from Entra ID (or existing group IDs from `doc-permission-mappings`)

## Overview

The daily sync Lambda (`sp-ingest-daily-sync`) uses the Microsoft Graph delta API,
which automatically discovers new document libraries within the configured SharePoint
site. **No code changes are required** to begin ingesting from a new library. However,
you must configure the permission mapping so that access control is enforced correctly.

## Step 1: Create S3 Prefix

Create the S3 prefix structure that matches the SharePoint library path.

The path follows the convention: `source/{SiteName}/{LibraryName}/`

Library names are sanitized: spaces become hyphens, special characters are stripped.

```bash
# Create the library prefix
aws s3api put-object \
  --bucket dynamo-ai-documents \
  --key "source/Dynamo/New-Library-Name/"
```

Replace `New-Library-Name` with the sanitized library name (e.g., SharePoint library
"Finance Reports" becomes `Finance-Reports`).

**Verify the prefix was created:**

```bash
aws s3 ls s3://dynamo-ai-documents/source/Dynamo/New-Library-Name/
```

## Step 2: Add Permission Mapping to DynamoDB

Insert a record into the `doc-permission-mappings` table to map the S3 prefix
to the allowed Entra ID group(s):

```bash
aws dynamodb put-item \
  --table-name doc-permission-mappings \
  --item '{
    "s3_prefix": {"S": "source/Dynamo/New-Library-Name/"},
    "allowed_groups": {"L": [{"S": "ENTRA_GROUP_OBJECT_ID"}]},
    "sensitivity_level": {"S": "internal"},
    "description": {"S": "Finance Reports document library"}
  }'
```

Replace:
- `New-Library-Name` with the sanitized library name
- `ENTRA_GROUP_OBJECT_ID` with the Entra ID group Object ID that should access these documents
- `internal` with the appropriate sensitivity level (`public`, `internal`, or `confidential`)
- The description with a meaningful label for this library

**For multiple groups with access:**

```bash
aws dynamodb put-item \
  --table-name doc-permission-mappings \
  --item '{
    "s3_prefix": {"S": "source/Dynamo/New-Library-Name/"},
    "allowed_groups": {"L": [
      {"S": "PRIMARY_GROUP_ID"},
      {"S": "LEADERSHIP_GROUP_ID"},
      {"S": "ADDITIONAL_GROUP_ID"}
    ]},
    "sensitivity_level": {"S": "confidential"},
    "description": {"S": "Finance Reports - restricted to finance team and leadership"}
  }'
```

**Verify the mapping was written:**

```bash
aws dynamodb get-item \
  --table-name doc-permission-mappings \
  --key '{"s3_prefix": {"S": "source/Dynamo/New-Library-Name/"}}' \
  --output json | python3 -m json.tool
```

## Step 3: Run Permission Drift Detector

Run the drift detector to confirm the new mapping is correctly registered and
no prefixes are unmapped:

```bash
aws lambda invoke \
  --function-name sp-ingest-permission-drift-detector \
  --payload '{}' \
  --cli-binary-format raw-in-base64-out \
  /dev/stdout 2>/dev/null | python3 -m json.tool
```

The response should show no unmapped prefixes. If the new prefix appears as
unmapped, verify that:
- The `s3_prefix` value exactly matches the S3 prefix (including trailing `/`)
- The S3 prefix object from Step 1 exists

## Step 4: Ingest Documents

Documents from the new library will be ingested in one of two ways:

### Option A: Wait for the daily sync (automatic)

The daily sync runs at 7 AM UTC via EventBridge. If the library is part of the
configured SharePoint site (`Dynamo`), the Graph delta API will automatically
discover and ingest all documents from the new library on the next run.

### Option B: Trigger a manual sync (immediate)

```bash
aws lambda invoke \
  --function-name sp-ingest-daily-sync \
  --payload '{}' \
  --cli-binary-format raw-in-base64-out \
  /dev/stdout 2>/dev/null | python3 -m json.tool
```

**Check the sync logs to verify the new library was discovered:**

```bash
aws logs filter-log-events \
  --log-group-name /aws/lambda/sp-ingest-daily-sync \
  --start-time $(python3 -c "import time; print(int((time.time() - 3600) * 1000))") \
  --filter-pattern '"New-Library-Name"' \
  --query "events[].message" --output text
```

### Option C: Bulk ingestion for large libraries

For libraries with thousands of documents, consider using the bulk ingestion
EC2 path for faster initial load. See `docs/ingestion-runbook.md` for bulk
ingestion instructions.

## Step 5: Verify Documents Are Being Processed

After ingestion, verify that documents are flowing through the pipeline:

**Check S3 for source documents:**

```bash
aws s3 ls s3://dynamo-ai-documents/source/Dynamo/New-Library-Name/ --recursive --summarize
```

**Check the document registry:**

```bash
aws dynamodb scan \
  --table-name sp-ingest-document-registry \
  --filter-expression "begins_with(s3_source_key, :prefix)" \
  --expression-attribute-values '{":prefix": {"S": "source/Dynamo/New-Library-Name/"}}' \
  --projection-expression "s3_source_key, textract_status, file_type" \
  --output table
```

**Check for extracted twins (digital twin JSON files):**

```bash
aws s3 ls s3://dynamo-ai-documents/extracted/Dynamo/New-Library-Name/ --recursive --summarize
```

**Check Textract pipeline status for this library's documents:**

```bash
aws dynamodb scan \
  --table-name sp-ingest-document-registry \
  --filter-expression "begins_with(s3_source_key, :prefix)" \
  --expression-attribute-values '{":prefix": {"S": "source/Dynamo/New-Library-Name/"}}' \
  --select COUNT
```

If documents show `textract_status` of `failed`, use the retry script:

```bash
./scripts/retry-failed-textract.sh --dry-run
```

## Step 6: Validate Query Access

Run a test query scoped to the new library prefix to verify end-to-end access:

**Query documents by library using the GSI:**

```bash
aws dynamodb query \
  --table-name sp-ingest-document-registry \
  --index-name sp_library-index \
  --key-condition-expression "sp_library = :lib" \
  --expression-attribute-values '{":lib": {"S": "New Library Name"}}' \
  --projection-expression "s3_source_key, textract_status, sp_last_modified" \
  --select SPECIFIC_ATTRIBUTES \
  --output table
```

Note: Use the original SharePoint library name (with spaces) for the GSI query,
not the sanitized S3 prefix name.

**Verify a specific document's twin was created:**

```bash
# Pick an s3_source_key from the query above and check for its twin
aws s3 ls "s3://dynamo-ai-documents/extracted/Dynamo/New-Library-Name/" --recursive | head -5
```

## Verification Checklist

- [ ] S3 prefix `source/Dynamo/{LibraryName}/` exists
- [ ] DynamoDB `doc-permission-mappings` entry created with correct groups and sensitivity
- [ ] Drift detector reports no unmapped prefixes
- [ ] Daily sync or manual sync discovered the library
- [ ] Source documents appear in S3 under the correct prefix
- [ ] Document registry entries exist with appropriate `textract_status`
- [ ] Extracted twins appear in `extracted/Dynamo/{LibraryName}/`
- [ ] No failed Textract jobs (or failures retried successfully)

## Troubleshooting

| Symptom | Cause | Resolution |
|---------|-------|------------|
| Library not discovered by daily sync | Library is on a different SharePoint site | Update the `SHAREPOINT_SITE_NAME` Lambda environment variable or add the site to the sync configuration |
| Documents uploaded to S3 but no twin in `extracted/` | Textract pipeline issue | Check `textract_status` in registry; look at `/aws/lambda/sp-ingest-textract-trigger` logs |
| Drift detector shows unmapped prefix | `s3_prefix` mismatch in DynamoDB | Verify the prefix value matches exactly, including case and trailing `/` |
| Large library causing Lambda timeout | Too many documents for Lambda's 900s timeout | Use bulk ingestion via EC2 (`docs/ingestion-runbook.md`) for initial load |
| S3 prefix name does not match SharePoint library name | Path sanitization rules | Spaces become hyphens, special characters stripped; see `docs/EXISTING_ARCHITECTURE.md` for sanitization rules |
| Documents in excluded folders not ingested | `EXCLUDED_FOLDERS` env var on daily-sync Lambda | Check and update if needed: `aws lambda get-function-configuration --function-name sp-ingest-daily-sync --query "Environment.Variables.EXCLUDED_FOLDERS"` |

## Related Runbooks

- [Add New Department](ADD_NEW_DEPARTMENT.md) -- Similar process for department-level setup
- [SharePoint Ingestion Pipeline Operations](../ingestion-runbook.md) -- Detailed pipeline operations and troubleshooting
- [Existing Architecture](../EXISTING_ARCHITECTURE.md) -- Full architecture reference
