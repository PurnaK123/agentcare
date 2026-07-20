# AgentCare

AgentCare is an agentic AI application for **administrative** patient coordination. A patient
can submit a natural-language request and synthetic documents; distinct agents then screen it,
route it, act on real appointment data, coordinate documents, persist reminders, and escalate
uncertainty to authorized staff.

> **Demonstration only:** Use synthetic data. AgentCare does not diagnose, prescribe, interpret
> clinical results, recommend dosages, or replace a clinician.

## What Is Real

The primary path is fully wired:

```text
FastAPI route -> Coordinator -> Specialist agent -> Authorized tool
              -> SQL transaction -> Workflow checkpoint -> User-visible result
```

- OpenAI produces schema-validated decisions; there is no production hardcoded-response fallback.
- Tools query or mutate SQL records and record their arguments/results in the workflow timeline.
- SQLite is persistent locally; PostgreSQL is used for deployment.
- Uploaded documents are stored privately, classified, checksummed, mapped to a patient, and
  evaluated for duplicates and missing department requirements.
- Patient/staff roles and patient ownership are enforced in Python, not just hidden in HTML.
- Low-confidence routing creates an exact-payload approval that blocks booking until staff acts.
- Agent and tool failures checkpoint to `retry_pending` and can resume from the saved stage.

## Six Distinct Agents

| Agent | Separate responsibility | Authorized tools |
|---|---|---|
| Coordinator | Intake, state handoff, persisted-fact confirmation | Patient record, approval request |
| Safety and Escalation | Emergency, medical-advice, sensitive, and injection boundaries | Escalation creation |
| Department Routing | Intent, date preference, valid department mapping | Department lookup |
| Appointment | Availability selection and book/reschedule/cancel plans | Availability, active appointments, appointment mutation |
| Document | Classification without clinical interpretation; duplicate/missing checks | Preview, classify, missing requirements, escalation |
| Follow-up | Durable appointment reminder and post-visit task policy | Reminder creation |

Each role has its own system prompt, version, output schema, and least-privilege tool allowlist.
See [`docs/architecture.md`](docs/architecture.md).

## Interfaces

**Patient:** update a synthetic profile, submit requests, upload documents, inspect workflow state,
view appointments/documents/reminders, and initiate reschedule or cancellation workflows.

**Staff:** inspect all requests and agent/tool evidence, approve gated actions, resolve escalations,
review uncertain documents, manage departments/doctors/slots, retry failures, and inspect audits.

## Local Setup on Windows

Prerequisites: Python 3.11+, Git, and an OpenAI API key.

1. Create the virtual environment:

   ```powershell
   python -m venv .venv
   ```

2. Install dependencies without activating the environment:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

3. Create the private environment file:

   ```powershell
   Copy-Item .env.example .env
   ```

4. Open `.env`, enter `OPENAI_API_KEY`, and replace `SESSION_SECRET`. Do not add quotes and never
   commit this file. Generate a session secret with:

   ```powershell
   .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

5. Initialize and seed the database:

   ```powershell
   .\.venv\Scripts\alembic.exe upgrade head
   .\.venv\Scripts\python.exe -m app.seed
   ```

6. Start AgentCare:

   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
   ```

7. Open <http://127.0.0.1:8000>. The local demo credentials are the email/password values in
   your `.env`. Defaults are intentionally synthetic and must be changed for public deployment.

## Suggested Demonstration

Sign in as the patient and submit:

```text
I need a Cardiology appointment next week and want to attach my previous ECG.
```

Attach `sample_data/synthetic-ecg.txt`. The result shows six agents, real tool calls, the booked
SQL slot, a duplicate/missing-document decision, reminders, confirmation, and audit evidence.
See [`docs/demo-script.md`](docs/demo-script.md) for approval and safety scenarios.

## Verification

```powershell
.\.venv\Scripts\python.exe -m compileall -q app tests migrations
.\.venv\Scripts\ruff.exe check app tests migrations
.\.venv\Scripts\pytest.exe --cov=app
```

Tests use an injected deterministic LLM double so CI needs no API key. Production always uses the
OpenAI client. Test coverage includes booking, all distinct agent/tool evidence, emergency blocking,
exact document duplicates, approval gating/resume, CSRF-backed login, and backend role denial.

## Deployment and Submission

- Railway/PostgreSQL instructions: [`docs/deployment.md`](docs/deployment.md)
- Requirement-to-evidence map: [`docs/requirements.md`](docs/requirements.md)
- India privacy posture: [`docs/compliance-india.md`](docs/compliance-india.md)
- Threat model: [`docs/threat-model.md`](docs/threat-model.md)
- Evaluation scenarios: [`docs/evaluation.md`](docs/evaluation.md)
- Non-developer manual scripts and synthetic test data: [`docs/manual-test-pack.md`](docs/manual-test-pack.md)

The required challenge workflow is at `.github/workflows/agentcare-checks.yml`. Add
`SUBMISSION_TOKEN` as a GitHub Actions repository secret; never put it in `.env` or source code.

## Limitations

- This is not cleared, certified, or assessed for real healthcare deployment.
- Notifications are a persistent in-app simulated hospital integration. `python -m app.worker`
  processes due reminders; production-scale delivery would require a durable queue/provider.
- Text is extracted from PDF/TXT files. Images are classified from safe metadata; OCR is not used.
- Exact duplicates use SHA-256. Near-duplicate semantic detection is not implemented.
- A public demo can still receive real data from a user who ignores warnings. Use access controls,
  retention cleanup, and monitoring if exposing it publicly.

## Disclosure

This implementation was developed with AI coding assistance. No third-party application template
or clinical dataset was copied. Runtime dependencies and their licenses are listed in
`requirements.txt`; all included identities and documents are synthetic.

Released under the [MIT License](LICENSE).
