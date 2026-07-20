# Challenge Requirement Evidence

| Requirement | Implementation evidence |
|---|---|
| Python backend | FastAPI application in `app/main.py` and `app/web.py` |
| LLM integration | Official `openai` dependency and `OpenAIJsonClient` in `app/agents/client.py` |
| Three or more distinct agents | Six prompts/roles/schemas in `app/agents/specialists.py` |
| Three or more real tools | Database-backed allowlisted gateway operations in `app/tools.py` |
| Persistent SQL | SQLAlchemy models plus Alembic migration; SQLite local/PostgreSQL deployment |
| Persistent workflow state | `WorkflowRun.state/current_step/status` and ordered `AgentStep` records |
| Patient UI | Profile, request/upload, status, appointments, documents, reminders |
| Staff UI | Queue, request evidence, escalations, approvals, catalog, audit |
| Backend RBAC | `require_patient`, `require_staff`, and patient-scoped SQL predicates |
| Human escalation | Persisted `Escalation`, staff review route, emergency and document triggers |
| Gated approval | Low-confidence route creates payload-hashed approval before appointment stage |
| Audit logging | `AuditEvent` records actors/actions/entities/correlation/redacted metadata |
| Error recovery | Bounded LLM retries, failed steps, `retry_pending`, patient/staff resume routes |
| Environment configuration | `pydantic-settings`, `.env.example`, production startup validation |
| Synthetic data | Identities/slots from `app/seed.py`; files under `sample_data/` |
| Document coordination | Private storage, signatures, SHA-256 duplicates, classification, missing checks |
| Appointment lifecycle | Transactional book/reschedule/cancel in `app/services/appointments.py` |
| Confirmation/reminders | LLM confirmation from persisted facts; durable reminders and in-app notifications |
| Tests | Workflow, safety, duplicate, approval, web wiring, CSRF, and RBAC in `tests/` |
| Deployment | `Dockerfile`, `railway.toml`, PostgreSQL and volume instructions |
| Challenge CI | `.github/workflows/agentcare-checks.yml` |

## End-to-End Evidence Path

For a Cardiology request with an ECG:

1. `POST /patient/requests` authenticates a patient and verifies CSRF.
2. `WorkflowCoordinator.submit` creates `PatientRequest`, `WorkflowRun`, staged document, and audit.
3. Safety Agent either blocks/escalates or hands validated state to Routing Agent.
4. Routing Agent invokes the SQL department lookup and returns only a valid department.
5. Appointment Agent invokes SQL availability and booking tools; slot and appointment persist.
6. Document Agent extracts a redacted preview, classifies it, detects duplicates, and checks the
   department's deterministic required-document list.
7. Follow-up Agent creates idempotent SQL reminders.
8. Coordinator reads persisted facts and obtains an OpenAI confirmation; an in-app notification and
   full audit/timeline are persisted.
9. Patient and staff pages query those same records. No page uses fixed demonstration results.
