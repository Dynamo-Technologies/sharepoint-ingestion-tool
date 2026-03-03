# Monitoring, Compliance Reporting & Operational Runbooks — Design

> **Prompt 8 of 8** — Final production-readiness layer

**Goal:** Add CloudWatch monitoring/alerting, a monthly compliance report Lambda, 7 operational runbooks, and comprehensive documentation to make the system production-ready and maintainable.

---

## Architecture

```
EventBridge Schedules
    │
    ├── cron(0 6 1 * ? *)  → compliance-report-generator Lambda
    │                           ├── Reads CloudWatch Logs (query audit data)
    │                           ├── Reads DynamoDB (user-group-cache, permission-mappings)
    │                           ├── Reads S3 (governance-reports/drift-report-*.json)
    │                           ├── Writes JSON + Markdown to S3 governance-reports/
    │                           └── Publishes summary to SNS (governance-alerts)
    │
    └── (existing schedules for SCIM sync, drift detector, stale cleanup)

CloudWatch Dashboard (SP-Ingest-Pipeline, extended)
    │
    ├── Row 5: Query & Auth Metrics
    │     Query volume, null result rate, avg latency, guardrail activations
    │
    ├── Row 6: Governance Health
    │     SCIM sync health, cache freshness, quarantine count
    │
    └── Row 7: Lambda Errors & DLQ Depth
          All governance Lambda errors, DLQ message counts

CloudWatch Alarms → sp-ingest-alerts SNS → Email
    ├── CRITICAL: SCIM sync not completed in 2h
    ├── CRITICAL: Quarantine count > 0
    ├── HIGH: Permission null result rate > 20%
    ├── HIGH: Any DLQ depth > 0
    ├── MEDIUM: Query latency p99 > 10s
    ├── MEDIUM: Stale cache entries > 10%
    └── LOW: Guardrail PII redaction triggered
```

---

## Components

### 1. CloudWatch Dashboard Extension

Extend the existing `SP-Ingest-Pipeline` dashboard with 3 new rows:

**Row 5 — Query & Auth Metrics:**
- Query volume (requests/min) — metric filter on query handler log group
- Permission-scoped null results rate — metric filter matching `"result_type": "no_results"`
- Average query latency — metric filter extracting `response_latency_ms` from audit logs
- Bedrock guardrail activations — metric filter matching `guardrail_intervened`

**Row 6 — Governance Health:**
- SCIM sync last success — metric filter on group-cache-refresh matching "Cache refresh complete"
- Group cache freshness — custom metric from sync stats (updated vs unchanged)
- Quarantine document count — S3 metric on quarantine/ prefix

**Row 7 — Lambda Errors & DLQ Depth:**
- Lambda errors for all governance Lambdas
- SQS ApproximateNumberOfMessagesVisible for all DLQs

### 2. CloudWatch Alarms (8 total new)

All route to `sp-ingest-alerts` SNS topic.

| Severity | Alarm | Metric | Threshold |
|----------|-------|--------|-----------|
| CRITICAL | SCIM sync stale | group-cache-refresh invocations | <1 in 2h |
| CRITICAL | Quarantine detected | quarantine SNS publish count | >0 |
| HIGH | Permission null rate | no_results / total queries | >20% in 1h |
| HIGH | DLQ depth | SQS messages visible | >0 per DLQ |
| MEDIUM | Query latency p99 | query latency ms | >10000 |
| MEDIUM | Stale cache | cache entries > 1h old | >10% |
| LOW | PII redaction | guardrail activations | >0 (OK action) |

### 3. Compliance Report Lambda

**Handler:** `src/compliance_report_generator.py`
**Trigger:** EventBridge `cron(0 6 1 * ? *)` — 6 AM UTC on the 1st of each month
**Timeout:** 300s, Memory: 256 MB

**Report sections:**
1. Total queries + unique users (from CloudWatch Logs Insights on query handler logs)
2. Permission denial count (no_results from audit logs)
3. Quarantined documents (S3 ListObjectsV2 on quarantine/ prefix)
4. SCIM sync uptime (% of 15-min windows with successful invocations)
5. Group membership changes (from group-cache-refresh logs)
6. Drift detection summary (latest drift report from S3)

**Output:**
- `s3://[bucket]/governance-reports/compliance-YYYY-MM.json`
- `s3://[bucket]/governance-reports/compliance-YYYY-MM.md`
- SNS summary to governance-alerts topic

### 4. Operational Runbooks (7)

All in `docs/runbooks/` with numbered step-by-step instructions:

1. `ADD_NEW_DEPARTMENT.md` — Entra group → SCIM → S3 prefix → permission mapping → validate
2. `ONBOARD_NEW_USER.md` — Entra groups → verify SCIM → verify RAG access
3. `OFFBOARD_USER.md` — Disable Entra → verify cache cleanup → verify access revoked
4. `ADD_NEW_DOCUMENT_LIBRARY.md` — S3 prefix → permission mapping → drift detector → ingest
5. `HANDLE_QUARANTINED_DOCUMENT.md` — Identify prefix → create mapping → re-process
6. `INVESTIGATE_PERMISSION_DENIAL.md` — Lookup groups → compare tags → identify mismatch
7. `EMERGENCY_REVOKE_ACCESS.md` — Empty cache → disable IAM Identity Center → audit queries

### 5. Documentation

**README.md** — Full rewrite with:
- Architecture diagram (all components + data flows)
- Component reference (all Lambdas, tables, EventBridge rules)
- Environment variables
- Quick start deployment guide
- Links to all runbooks

**DEPLOYMENT_CHECKLIST.md** — Ordered steps from scratch:
1. Prerequisites (AWS account, Entra ID, Terraform)
2. Terraform infrastructure
3. Lambda deployment
4. SCIM configuration
5. Initial data load
6. Monitoring verification
7. Production go-live

### 6. Terraform IaC

**New files:**
- `terraform/lambda_compliance.tf` — Compliance report Lambda + EventBridge
- `terraform/iam_compliance.tf` — IAM role for compliance Lambda

**Modified files:**
- `terraform/monitoring.tf` — Extended dashboard + new alarms + metric filters
- `terraform/outputs.tf` — Compliance Lambda ARN output
- `terraform/variables.tf` — (no new variables needed; reuses existing)

---

## Testing Strategy

- **compliance_report_generator**: Mock CloudWatch Logs Insights, DynamoDB scans, S3 list/put. Test report JSON structure, Markdown formatting, SNS publish.
- **Terraform**: `terraform validate` for all new/modified .tf files.
- **Full regression**: Run full test suite to confirm nothing broken.

---

## Module Structure

```
src/
└── compliance_report_generator.py    # Monthly compliance report Lambda

tests/
└── test_compliance_report.py         # Tests for compliance report generator

terraform/
├── monitoring.tf                     # Extended dashboard + alarms (modified)
├── lambda_compliance.tf              # Compliance Lambda + EventBridge (new)
├── iam_compliance.tf                 # IAM role for compliance Lambda (new)
├── outputs.tf                        # New output (modified)
└── variables.tf                      # (unchanged)

docs/
├── README.md                         # Full project README (rewritten)
├── DEPLOYMENT_CHECKLIST.md           # Ordered deployment steps (new)
└── runbooks/
    ├── ADD_NEW_DEPARTMENT.md
    ├── ONBOARD_NEW_USER.md
    ├── OFFBOARD_USER.md
    ├── ADD_NEW_DOCUMENT_LIBRARY.md
    ├── HANDLE_QUARANTINED_DOCUMENT.md
    ├── INVESTIGATE_PERMISSION_DENIAL.md
    └── EMERGENCY_REVOKE_ACCESS.md
```
