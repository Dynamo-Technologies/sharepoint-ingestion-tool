# Handle Quarantined Document

## Description

A document has been moved to the `quarantine/` prefix in S3 because it could not be
matched to a valid permission mapping in the `doc-permission-mappings` DynamoDB table.
This runbook walks through identifying the document, resolving the missing mapping, and
re-ingesting the document into the pipeline.

## Severity

**Medium** -- Documents in quarantine are not searchable or accessible to end users. They
remain safely stored but are effectively invisible until remediated.

## Prerequisites

- AWS CLI v2 configured with credentials that have access to the `dynamo-ai-documents`
  S3 bucket, the `doc-permission-mappings` DynamoDB table, and the `sp-ingest-textract-trigger`
  Lambda function.
- Permissions: `s3:ListBucket`, `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`,
  `dynamodb:Scan`, `dynamodb:PutItem`, `lambda:InvokeFunction`.
- Access to the `doc-quarantine-alerts` SNS topic subscription (email or Slack) to
  receive quarantine notifications.

---

## Steps

### Step 1: Receive the quarantine alert

An alert arrives via the `doc-quarantine-alerts` SNS topic, a CloudWatch alarm, or
manual monitoring. The alert payload typically contains the S3 key of the quarantined
document.

If you do not have the specific key, proceed to Step 2 to list all quarantined documents.

---

### Step 2: List quarantined documents

```bash
aws s3 ls s3://dynamo-ai-documents/quarantine/ --recursive
```

Note the full key of the document you are investigating (for example,
`quarantine/source-Dynamo-HR-report.pdf`).

---

### Step 3: Determine the original S3 prefix

The quarantine key encodes the original prefix with hyphens replacing slashes. Reverse
the encoding to determine where the document should live.

**Example:**

| Quarantine Key | Original Prefix | Original Key |
|---|---|---|
| `quarantine/source-Dynamo-HR-report.pdf` | `source/Dynamo/HR/` | `source/Dynamo/HR/report.pdf` |
| `quarantine/source-Dynamo-Finance-Q4-budget.xlsx` | `source/Dynamo/Finance/` | `source/Dynamo/Finance/Q4-budget.xlsx` |

Record the original prefix (e.g., `source/Dynamo/HR/`) and the target filename.

---

### Step 4: Check whether a permission mapping exists for the prefix

```bash
aws dynamodb scan \
  --table-name doc-permission-mappings \
  --filter-expression "begins_with(s3_prefix, :p)" \
  --expression-attribute-values '{":p": {"S": "source/Dynamo/HR/"}}' \
  --output table
```

- If a mapping is returned, skip to Step 6 -- the document may have been quarantined due
  to a transient issue or a since-resolved race condition.
- If no mapping is returned, proceed to Step 5 to create one.

---

### Step 5: Create the missing permission mapping

Determine the appropriate `allowed_groups` and `sensitivity_level` for this prefix by
consulting the document owner, the `access_rules.yaml` configuration, or the
`PERMISSION_MAPPING_VALIDATION.md` report.

```bash
aws dynamodb put-item \
  --table-name doc-permission-mappings \
  --item '{
    "s3_prefix": {"S": "source/Dynamo/HR/"},
    "allowed_groups": {"SS": ["HR", "Leadership"]},
    "sensitivity_level": {"S": "confidential"},
    "created_at": {"S": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"},
    "created_by": {"S": "OPERATOR_NAME - quarantine remediation"}
  }'
```

Replace `allowed_groups` and `sensitivity_level` with the correct values for your use
case. Common sensitivity levels: `public`, `internal`, `confidential`, `restricted`.

---

### Step 6: Move the document back to the correct prefix

```bash
aws s3 mv \
  s3://dynamo-ai-documents/quarantine/source-Dynamo-HR-report.pdf \
  s3://dynamo-ai-documents/source/Dynamo/HR/report.pdf
```

Verify the file is in the correct location:

```bash
aws s3 ls s3://dynamo-ai-documents/source/Dynamo/HR/report.pdf
```

---

### Step 7: Re-trigger ingestion

Invoke the Textract trigger Lambda with a synthetic S3 event to kick off extraction:

```bash
aws lambda invoke \
  --function-name sp-ingest-textract-trigger \
  --cli-binary-format raw-in-base64-out \
  --payload '{
    "Records": [{
      "s3": {
        "bucket": {"name": "dynamo-ai-documents"},
        "object": {"key": "source/Dynamo/HR/report.pdf"}
      }
    }]
  }' \
  /dev/stdout 2>/dev/null | python3 -m json.tool
```

Check the Lambda response for errors. A successful invocation returns a JSON payload
with no `errorMessage` field.

---

### Step 8: Verify the document appears in search results

Wait 1-2 minutes for Textract to process the document (longer for large PDFs), then
confirm:

1. Check the document registry for a `completed` status:

   ```bash
   aws dynamodb get-item \
     --table-name sp-ingest-document-registry \
     --key '{"s3_source_key": {"S": "source/Dynamo/HR/report.pdf"}}' \
     --projection-expression "textract_status, s3_twin_key, updated_at" \
     --output table
   ```

2. Confirm the extracted twin exists in S3:

   ```bash
   aws s3 ls s3://dynamo-ai-documents/extracted/Dynamo/HR/report.pdf.json
   ```

3. Confirm the quarantine prefix is now empty (or that the specific file is gone):

   ```bash
   aws s3 ls s3://dynamo-ai-documents/quarantine/ | grep "report.pdf" || echo "Quarantine clear for this document"
   ```

---

## Verification Checklist

- [ ] The quarantine prefix no longer contains the remediated document.
- [ ] The document exists at the correct `source/` prefix.
- [ ] A valid permission mapping exists in `doc-permission-mappings` for the prefix.
- [ ] The document registry shows `textract_status = completed`.
- [ ] An extracted twin exists in the `extracted/` prefix.

---

## Troubleshooting

| Symptom | Likely Cause | Resolution |
|---------|-------------|------------|
| Textract trigger returns `errorMessage` | File type unsupported or S3 key malformed | Check file extension is one of `.pdf`, `.docx`, `.pptx`, `.xlsx`, `.txt`. Verify the S3 key is correct. |
| Registry shows `textract_status = failed` | Textract could not process the document (corrupted PDF, scanned image with no text) | Check CloudWatch logs at `/aws/lambda/sp-ingest-textract-complete` for the specific error. Retry with `textract_status = pending`. |
| Document keeps getting re-quarantined | Permission mapping prefix does not match the document key exactly | Run Step 4 again and verify the `s3_prefix` in the mapping is a true prefix of the document's S3 key. |
| Permission mapping exists but document was still quarantined | Race condition during ingestion -- mapping was created after the document was processed | Moving the document back (Step 6) and re-triggering (Step 7) resolves this. |
| Multiple documents quarantined from the same prefix | A bulk upload occurred before the mapping was created | Create the mapping (Step 5), then move all quarantined documents for that prefix in a batch using `aws s3 mv --recursive`. |
