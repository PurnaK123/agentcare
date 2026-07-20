# Railway Deployment

These steps deploy the challenge demo with PostgreSQL and private persistent file storage. Railway's
screen labels may change; do not paste secrets into source files or chat.

## 1. Prepare GitHub

1. Create an empty public GitHub repository.
2. Push this project to its default `main` branch.
3. In GitHub, open **Settings > Secrets and variables > Actions**.
4. Create repository secret `SUBMISSION_TOKEN` with the value from the challenge dashboard.
5. Open **Actions** and confirm both workflows start. The challenge workflow downloads and executes
   the organizer's checks with read-only repository permission and GitHub OIDC.

## 2. Create Railway Services

1. Sign in to Railway using GitHub.
2. Choose **New Project > Deploy from GitHub repo** and select the public AgentCare repository.
3. Add a **PostgreSQL** service to the same project.
4. Open the AgentCare service variables and expose the PostgreSQL connection as `DATABASE_URL` using
   Railway's variable-reference selector. Do not manually publish the database password.
5. Add a persistent volume to the AgentCare service and mount it at `/data`.

## 3. Configure Variables

Set these on the AgentCare service:

```text
APP_ENV=production
BASE_URL=https://your-generated-domain
DATABASE_URL=<reference to Railway PostgreSQL DATABASE_URL>
AUTO_CREATE_TABLES=false
SESSION_SECRET=<new random value of at least 32 characters>
COOKIE_SECURE=true
OPENAI_API_KEY=<your OpenAI key>
OPENAI_MODEL=gpt-4o-mini
UPLOAD_DIR=/data/uploads
STAGING_DIR=/data/staging
DEMO_MODE=true
SEED_DEMO_DATA=true
TIMEZONE=Asia/Kolkata
EMERGENCY_NUMBER=112
DEMO_PATIENT_EMAIL=asha.patient@example.test
DEMO_PATIENT_PASSWORD=<new synthetic-demo password>
DEMO_STAFF_EMAIL=meera.staff@example.test
DEMO_STAFF_PASSWORD=<different synthetic-demo password>
```

Generate each secret locally:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(48))"
```

Never reuse an OpenAI, GitHub, email, or personal password for a demo account.

## 4. Deploy and Verify

1. Deploy. The `Dockerfile` runs `alembic upgrade head` before starting the server.
2. Generate a Railway public domain for the web service.
3. Set `BASE_URL` to that exact HTTPS URL and redeploy.
4. Open `/health`; verify `status=ok`, `database=connected`, and `llm_configured=true`.
5. Sign in with both synthetic accounts and run the main flow in `docs/demo-script.md`.
6. Restart the Railway service and verify the request, appointment, timeline, and document remain.
7. Check OpenAI usage limits and set a small project budget alert.

## 5. Reminder Worker

The core challenge persists reminders immediately. To demonstrate due delivery, run this Railway
one-off command or scheduled job:

```text
python -m app.worker
```

It creates idempotent in-app notifications and marks due reminders sent. A production system would
use a durable queue and contracted notification provider.

## Submission Safety

- Share only synthetic demo credentials with judges.
- Do not expose Railway/PostgreSQL/OpenAI credentials.
- Keep the demo banner enabled and do not invite public real-world use.
- Review uploaded demo records and delete the Railway project after judging if no longer needed.
