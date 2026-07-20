# Threat Model

## Protected Assets

Synthetic patient profiles, requests, documents, appointments, workflow/approval state, sessions,
OpenAI key, submission token, staff privileges, and audit integrity.

## Trust Boundaries

1. Browser to FastAPI over HTTPS.
2. FastAPI route to authenticated role/ownership dependency.
3. Coordinator to untrusted LLM output.
4. Agent to least-privilege ToolGateway.
5. Services to SQL database and private document storage.
6. Deployment to OpenAI and GitHub challenge services.

## Principal Threats and Controls

| Threat | Controls |
|---|---|
| Patient accesses another patient | Session identity plus patient ID in every ownership query |
| Patient calls staff action directly | Backend `require_staff`; CSRF on mutations |
| Role forged in HTML/request | Role loaded from SQL; never accepted from form or model |
| Prompt injection in request/document | Untrusted-data prompt boundaries, deterministic signals, structured output, no direct model tools, escalation |
| Model invents department/record ID | Validate against current tool-returned database allowlist |
| Double booking | Slot row lock/status transition and unique active slot assignment |
| Duplicate side effect after retry | Workflow/action and reminder/notification idempotency keys |
| Approval changed after review | Canonical payload SHA-256 verified immediately before review/resume |
| Malicious upload/path traversal | Extension and signature allowlist, size cap, basename stripping, random names, private root containment |
| Document disclosure | Authenticated download plus patient ownership/staff role |
| Secrets or document bodies in logs | Metadata redaction and compact summaries; `.env`/uploads ignored |
| Agent loop/resource exhaustion | Fixed state graph, `MAX_WORKFLOW_STEPS`, request/file limits, bounded API retries |
| Provider outage or malformed JSON | Retry, rollback, failed `AgentStep`, durable checkpoint and manual resume |
| Unsafe medical behavior | Deterministic emergency gate, separate Safety Agent, no clinical tools, human escalation |

## Residual Risks

- No network-level rate limiter or MFA is included in the six-day demonstration.
- File allowlisting is not a replacement for antivirus/content-disarm scanning.
- Image OCR and near-duplicate detection are intentionally out of scope.
- SQLite offers weaker concurrent-write behavior than deployment PostgreSQL.
- A user may ignore warnings and enter real data; access restriction and retention cleanup are still
  needed on any public deployment.
- LLM safety and correctness remain probabilistic; deterministic validation reduces but cannot
  eliminate model risk.
