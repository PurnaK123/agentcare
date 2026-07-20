# Evaluation Strategy

Automated tests inject a deterministic LLM test double at the same typed client boundary used by
OpenAI. This makes behavior reproducible without presenting fake responses in production.

## Implemented Scenarios

- Complete request: registration lookup, safety, Cardiology route, real slot booking, ECG
  classification, missing blood report, two reminders, confirmation, six agent records, tool calls.
- Emergency wording: deterministic block, critical escalation, no appointment, India emergency
  instruction.
- Exact duplicate: renamed identical bytes produce a duplicate record with original provenance and
  no second stored object.
- Human gate: low-confidence valid route creates a payload-hashed approval; no booking occurs before
  staff approval; the persisted workflow resumes after approval.
- Browser wiring: signed login and CSRF submit a request through the real route/orchestrator/services;
  resulting agent and booking evidence renders in HTML.
- RBAC: patient receives 403 from staff UI, staff receives 403 from patient UI, anonymous users are
  redirected to sign-in.

## Recommended Manual Checks Before Submission

1. OpenAI live call with the configured model and a safe synthetic request.
2. Deliberately ambiguous department request and staff approval.
3. Emergency request and confirmation that no appointment exists.
4. Upload both supplied synthetic files, then upload the ECG again under a different filename.
5. Stop/restart the service and confirm requests, timeline, documents, and appointments persist.
6. Temporarily use an invalid model name, confirm `retry_pending`, restore it, and retry from UI.
7. Run two booking attempts for the same slot against PostgreSQL and confirm only one succeeds.
8. Verify GitHub Actions challenge and application workflows are green.
